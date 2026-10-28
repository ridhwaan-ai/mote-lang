"""Mote app v0.5 - loads CSVs into columns, runs Mote programs (reads and writes), logs every run.

  python mote_app.py serve [--data data] [--port 8765]   open http://127.0.0.1:8765
  python mote_app.py run "@tickets|C" [--data data]      one-off from the command line (changes are PREVIEW only)
  python mote_app.py run "..." --apply [--who name]      really save create/insert/update/delete changes (backup kept)
  python mote_app.py run-file prog.mote [--data d]       run a program saved in a file (no shell escaping to worry about)
  python mote_app.py restore <table> [--data data]       put back the newest backup of a table
  python mote_app.py help SYMBOL                         details for one step or function

Every CSV in the data folder becomes a source named after the file (tickets.csv -> @tickets).
Runs are appended to mote_log.jsonl. The server only listens on this computer (127.0.0.1).
"""
import glob
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

import mote
import mote_fast
import mote_flow

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "mote_log.jsonl")
MAX_SHOW = 500


class Store:
    """CSV folder -> DataFrames, re-read only when a file changes. Writes always back up first."""

    def __init__(self, folder):
        self.folder, self.cache = folder, {}
        self.backups = os.path.join(folder, "_backups")

    def _path(self, name):
        if not re.fullmatch(r"\w+", name):
            raise mote.MoteError(f"bad table name '{name}'")
        return os.path.join(self.folder, name + ".csv")

    def _backup(self, name):
        p = self._path(name)
        if not os.path.exists(p):
            return None
        os.makedirs(self.backups, exist_ok=True)
        b = os.path.join(self.backups, f"{name}.{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1e6) % 1000000:06d}.csv")
        shutil.copy2(p, b)
        return os.path.basename(b)

    def write(self, name, df):
        """Save a table as CSV (temp file then atomic replace). Returns the backup file name, if any."""
        os.makedirs(self.folder, exist_ok=True)
        b = self._backup(name)
        tmp = self._path(name) + ".tmp"
        df.to_csv(tmp, index=False)
        os.replace(tmp, self._path(name))
        self.cache.pop(name, None)
        return b

    def write_file(self, path, text):
        """Write a text file inside the data folder (temp file, then atomic replace). An existing file is copied to _backups first."""
        backup = None
        if os.path.exists(path):
            os.makedirs(self.backups, exist_ok=True)
            rel = os.path.relpath(path, os.path.realpath(self.folder)).replace("\\", "/").replace("/", "__")
            backup = f"{rel}.{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1e6) % 1000000:06d}"
            shutil.copy2(path, os.path.join(self.backups, backup))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
        return backup

    def drop(self, name):
        b = self._backup(name)
        if os.path.exists(self._path(name)):
            os.remove(self._path(name))
        self.cache.pop(name, None)
        return b

    def restore(self, name):
        """Put back the newest backup of a table (the current file is backed up first)."""
        found = sorted(glob.glob(os.path.join(self.backups, name + ".*.csv")))
        if not found:
            raise mote.MoteError(f"no backup found for '{name}'")
        df = pd.read_csv(found[-1])
        self.write(name, df)
        return os.path.basename(found[-1])

    def sources(self):
        out = {}
        for p in sorted(glob.glob(os.path.join(self.folder, "*.csv"))):
            name = os.path.splitext(os.path.basename(p))[0]
            if not re.fullmatch(r"\w+", name):
                continue
            m = os.path.getmtime(p)
            if name not in self.cache or self.cache[name][0] != m:
                self.cache[name] = (m, pd.read_csv(p))
            out[name] = self.cache[name][1]
        return out


def clean(x):
    """Make a result safe for JSON (NaN -> null)."""
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, list):
        return [{k: clean(v) for k, v in r.items()} for r in x]
    return x


def log(entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def collapse(effects):
    """One change per table (the last one wins); a table created and dropped in one run changes nothing."""
    final = {}
    for e in effects:
        first = final.get(e["name"])
        final[e["name"]] = {**e, "before": first["before"] if first else e["before"],
                            "first_op": first["first_op"] if first else e["op"]}
    return [e for e in final.values() if not (e["op"] == "drop" and e["first_op"] == "create")]


def run_program(program, store, apply=False, who="ui"):
    """Run an Mote program. Changes (create/insert/update/delete/save) are only written to disk if apply=True;
    otherwise the result is a preview and nothing on disk changes."""
    t0 = time.perf_counter()
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "who": who, "apply": bool(apply), "program": program}
    try:
        src = store.sources()
        entry["rows_in"] = {k: len(v) for k, v in src.items()}
        effects = []
        result = clean(mote_fast.run(program, src, effects, base=store.folder))
        entry["ok"] = True
        entry["result_sha"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:12]
        out = {"ok": True, "result": result}
        changes = []
        io_fx = [x for x in effects if x["op"] in ("file", "post")]
        for e in collapse([x for x in effects if x["op"] not in ("file", "post")]):
            c = {"table": e["name"], "op": e["op"], "rows_before": e["before"],
                 "rows_after": 0 if e["df"] is None else len(e["df"])}
            if apply:
                c["backup"] = store.drop(e["name"]) if e["op"] == "drop" else store.write(e["name"], e["df"])
            changes.append(c)
        done = []
        for e in sorted(io_fx, key=lambda x: x["op"] != "file"):        # files first, posts last (a post cannot be undone)
            c = {"table": e["name"], "op": "write file" if e["op"] == "file" else "post", "rows_before": 0, "rows_after": e["rows"]}
            if apply:
                try:
                    if e["op"] == "file":
                        c["backup"] = store.write_file(e["path"], e["text"])
                    else:
                        c["status"] = mote_flow.do_post(e["url"], e["body"])
                except mote.MoteError as err:
                    if done:
                        raise mote.MoteError(f"{err}. Already done in this run and kept: {', '.join(done)}")
                    raise
                done.append(e["name"])
            changes.append(c)
        if changes:
            out["changes"] = changes
            out["saved"] = bool(apply)
            entry["writes"] = changes
        elif apply:
            out["saved"] = False
    except mote.MoteError as e:
        entry.update(ok=False, error=str(e))
        out = {"ok": False, "error": str(e)}
    except Exception as e:  # keep the app alive; the log keeps the evidence
        entry.update(ok=False, error=f"internal error {type(e).__name__}: {e}")
        out = {"ok": False, "error": entry["error"]}
    entry["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    out["ms"] = entry["ms"]
    log(entry)
    return out


def describe(store):
    return {k: {"rows": len(v), "fields": [f"{c} ({v[c].dtype})" for c in v.columns]} for k, v in store.sources().items()}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mote</title><style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--card:#f4f4f2;--line:#d8d8d4;--acc:#b8860b;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#0e0e0e;--fg:#ececec;--mut:#9a9a9a;--card:#1b1b1b;--line:#333;--acc:#d4af37;--bad:#ff7b72}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:900px;margin:0 auto;padding:20px 16px}
h1{font-size:22px;margin:0 0 4px}h1 b{color:var(--acc)}p.s{color:var(--mut);margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:14px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 8px}
textarea{width:100%;box-sizing:border-box;min-height:110px;font:14px ui-monospace,Consolas,monospace;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px}
button{background:var(--acc);color:#000;border:0;border-radius:6px;padding:8px 18px;font-weight:600;cursor:pointer;margin-top:8px}
code{font-family:ui-monospace,Consolas,monospace;font-size:13px}.src{margin:0 0 4px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}
.err{color:var(--bad);white-space:pre-wrap}.mut{color:var(--mut)}.wrap{overflow-x:auto}
</style></head><body><main>
<h1><b>Mote</b> app</h1><p class="s">Write an Mote program. It runs on columns, checks every step, and logs the run.</p>
<div class="card"><h2>Sources (CSV files in the data folder)</h2><div id="src" class="mut">loading...</div></div>
<div class="card"><h2>Program</h2><textarea id="prog" spellcheck="false">@tickets|F(status=="open")|C</textarea>
<button id="go">Run</button> <label class="mut"><input type="checkbox" id="apply"> Save changes to disk (unticked = preview only, nothing changes)</label> <span class="mut" id="hint">Ctrl+Enter runs</span></div>
<div class="card"><h2>Result</h2><div id="out" class="mut">Nothing run yet.</div></div>
<div class="card"><h2>Recent runs</h2><div id="hist" class="mut"></div></div></main>
<script>
const $=id=>document.getElementById(id);
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e}
async function src(){const d=await(await fetch('/sources')).json();const b=$('src');b.textContent='';
 const k=Object.keys(d);if(!k.length){b.textContent='No CSV files found.';return}
 k.forEach(n=>{const p=el('p','src');p.append(el('code','','@'+n),' '+d[n].rows.toLocaleString()+' rows: '+d[n].fields.join(', '));b.append(p)})}
async function hist(){const d=await(await fetch('/history')).json();const b=$('hist');b.textContent='';
 if(!d.length){b.textContent='No runs yet.';return}
 d.forEach(r=>{const p=el('p','src');p.append(el('code','',(r.ok?'OK  ':'ERR ')+r.program.replace(/\n/g,' ; ')),el('span','mut','  ['+(r.who||'?')+(r.apply?', saved':'')+'] '+r.time+'  '+r.ms+' ms'));b.append(p)})}
async function run(){const r=await(await fetch('/run',{method:'POST',body:JSON.stringify({program:$('prog').value,apply:$('apply').checked,who:'you'})})).json();
 const o=$('out');o.textContent='';
 if(!r.ok){o.append(el('div','err',r.error));o.append(el('div','mut',r.ms+' ms'));hist();return}
 if(r.changes){const m=el('div',r.saved?'':'mut');m.style.cssText='margin-bottom:8px;font-weight:600';
  m.textContent=(r.saved?'SAVED: ':'PREVIEW ONLY (nothing saved, tick the box to save): ')+r.changes.map(c=>c.op+' '+c.table+' ('+c.rows_before+' -> '+c.rows_after+' rows)').join('; ');o.append(m)}
 const v=r.result;
 if(Array.isArray(v)){if(!v.length){o.append(el('div','','(empty table)'))}else{
  const cols=Object.keys(v[0]),w=el('div','wrap'),t=el('table'),h=el('tr');cols.forEach(c=>h.append(el('th','',c)));t.append(h);
  v.slice(0,500).forEach(row=>{const tr=el('tr');cols.forEach(c=>tr.append(el('td','',row[c]===null?'':String(row[c]))));t.append(tr)});
  w.append(t);o.append(w);if(v.length>500)o.append(el('div','mut','showing first 500 of '+v.length+' rows'))}}
 else{const d=el('div','',String(v));d.style.cssText='font-size:28px;font-weight:700';o.append(d)}
 o.append(el('div','mut',r.ms+' ms'));hist();src()}
$('go').onclick=run;$('prog').addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter')run()});src();hist();
</script></body></html>"""


def make_handler(store):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE, "text/html")
            elif self.path == "/sources":
                self._send(200, json.dumps(describe(store)))
            elif self.path == "/history":
                rows = []
                if os.path.exists(LOG):
                    rows = [json.loads(l) for l in open(LOG, encoding="utf-8").read().splitlines()[-8:]]
                self._send(200, json.dumps(rows[::-1]))
            else:
                self._send(404, "{}")

        def do_POST(self):
            if self.path != "/run":
                return self._send(404, "{}")
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n))
                prog = body["program"]
            except Exception:
                return self._send(400, json.dumps({"ok": False, "error": "bad request"}))
            self._send(200, json.dumps(run_program(prog, store, apply=bool(body.get("apply")), who=str(body.get("who", "ui"))[:20])))

        def log_message(self, *a):
            pass

    return H


def help_text(sym):
    """Details for one symbol or function, pulled from the full guide (GUIDE_FULL.md), so a short guide can stay short."""
    p = os.path.join(HERE, "GUIDE_FULL.md")
    if not os.path.exists(p):
        p = os.path.join(HERE, "GUIDE.md")
    lines = open(p, encoding="utf-8").read().splitlines()
    sym = sym.strip().rstrip("()")
    if not sym:
        return "Usage: python mote_app.py help SYMBOL   (for example: help L, help Y, help days, help blank)"
    keys = [f"`{sym}", f"{sym}(", f"`{sym}("]
    hits = [l.strip().strip("|").replace("|", " : ") for l in lines
            if any(k in l for k in keys) or (sym.lower() in ("blank", "blanks", "null") and "Blank" in l)
            or (sym.lower() in ("round", "rounding") and "round" in l.lower())]
    return chr(10).join(hits) if hits else f"No entry for '{sym}'. Try a symbol like F, J, L, Z or a function like days."


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


if __name__ == "__main__":
    store = Store(os.path.abspath(arg("--data", os.path.join(HERE, "data"))))
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        port = int(arg("--port", 8765))
        print(f"Mote app on http://127.0.0.1:{port}  data: {store.folder}")
        ThreadingHTTPServer(("127.0.0.1", port), make_handler(store)).serve_forever()
    elif len(sys.argv) > 2 and sys.argv[1] == "run":
        r = run_program(sys.argv[2], store, apply="--apply" in sys.argv, who=arg("--who", "cli"))
        print(json.dumps(r, indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "run-file":
        with open(sys.argv[2], encoding="utf-8") as fh:
            r = run_program(fh.read(), store, apply="--apply" in sys.argv, who=arg("--who", "cli"))
        print(json.dumps(r, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "help":
        print(help_text(sys.argv[2] if len(sys.argv) > 2 else ""))
    elif len(sys.argv) > 2 and sys.argv[1] == "restore":
        try:
            print("restored from", store.restore(sys.argv[2]))
        except mote.MoteError as e:
            print("ERROR:", e)
    else:
        print(__doc__)
