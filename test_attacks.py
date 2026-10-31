"""Attack tests: things a hostile or careless agent might try. Each must be refused (or contained) and leave nothing behind.
Run: python test_attacks.py"""
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mote
import mote_app
import mote_flow

bad = 0


def check(name, ok, detail=""):
    global bad
    bad += not ok
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)[:200]))


HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
data = os.path.join(tmp, "data")
shutil.copytree(os.path.join(HERE, "data"), data)
outside = os.path.join(tmp, "outside")          # a sibling folder the engine must never touch
os.makedirs(outside)
open(os.path.join(outside, "secret.txt"), "w").write("TOP SECRET")
store = mote_app.Store(data)


def run(prog, apply=True):
    return mote_app.run_program(prog, store, apply=apply, who="attacker")


def refused(prog, apply=True):
    r = run(prog, apply)
    return not r.get("ok")


def exists_outside():
    return sorted(os.listdir(outside))


# ---- 1. path traversal on files (read and write) ----
for p in ['@text("../outside/secret.txt")', '@text("..\\\\outside\\\\secret.txt")', '@json("../outside/secret.txt")',
          '@dir("../outside")', '@text("/etc/passwd")', '@text("C:/Windows/win.ini")', '@text("\\\\\\\\localhost\\\\c$\\\\x")']:
    r = run(p, False)
    check("read refused: " + p, not r.get("ok") or "TOP SECRET" not in json.dumps(r), r)
for rel in ["../outside/x.txt", "..\\\\outside\\\\x.txt", "sub/../../outside/x.txt", "/tmp/x.txt", "C:/x.txt", "C:\\\\x.txt"]:
    run(f'@const("pwned")|WF("{rel}")')
    check("write refused: " + rel, exists_outside() == ["secret.txt"] and not os.path.exists("/tmp/x.txt") and not os.path.exists("C:/x.txt"), exists_outside())

# ---- 2. dangerous file types, including Windows tricks ----
for name in ["a.py", "a.PY", "a.py.", "a.py ", "a.py...", "a.py::$DATA", "a.csv:evil.py", "a.bat", "a.ps1", "a.pth", "a.hta", "a.reg", "a.vbe", "a.wsf", "a.psm1", "a.pyw", "a.js", "a.lnk",
             "run.cmd", "a.scr", "a.msi", "a.dll", "a.exe", "a.jar", "a.sh", "a.php", "a.html", "a.svg", "a.htm", "a.xml"]:
    run(f'@const("x")|WF("{name}")')
    check("blocked type not written: " + name, not any(os.path.exists(os.path.join(data, n)) for n in os.listdir(data) if n.lower().startswith("a.") or n == "run.cmd"),
          [n for n in os.listdir(data) if n.lower().startswith("a.")])
# harmless types still work
r = run('@tickets|WF("reports/ok.csv")')
check("normal write still works", r.get("ok") and os.path.exists(os.path.join(data, "reports", "ok.csv")), r)

# ---- 3. protected folders ----
for rel in ["_backups/x.csv", "_backups/../x.txt", "allowed_hosts.txt"]:
    run(f'@const("x")|WF("{rel}")')
check("cannot write into _backups", not os.path.exists(os.path.join(data, "_backups", "x.csv")))

# ---- 4. table names and restore ----
for prog in ['I("../evil",a)', 'I(evil/../x,a)', "I(a b,a)", "I(x;y,a)"]:
    check("bad table name refused: " + prog, refused(prog) or not os.path.exists(os.path.join(tmp, "evil.csv")), prog)
for t in ["../outside/secret", "..\\\\x", "*", "?", "a/../../x"]:
    try:
        store.restore(t)
        check("restore refuses " + t, False)
    except Exception:
        check("restore refuses " + t, True)

# ---- 5. web: allow-list, redirects, schemes, userinfo tricks ----
hits = {"other": 0, "main": 0}


class Other(BaseHTTPRequestHandler):
    def do_GET(self):
        hits["other"] += 1
        self.send_response(200); self.end_headers(); self.wfile.write(b'[{"secret": 1}]')

    do_POST = do_GET

    def log_message(self, *a):
        pass


class Main(BaseHTTPRequestHandler):
    def do_GET(self):
        hits["main"] += 1
        self.send_response(302); self.send_header("Location", "http://localhost:8797/stolen"); self.end_headers()

    do_POST = do_GET

    def log_message(self, *a):
        pass


s_other = ThreadingHTTPServer(("127.0.0.1", 8797), Other)
s_main = ThreadingHTTPServer(("127.0.0.1", 8796), Main)
for s in (s_other, s_main):
    threading.Thread(target=s.serve_forever, daemon=True).start()

mote_flow.ALLOWED_HOSTS = []
check("no hosts allowed by default", refused('@get("http://127.0.0.1:8796/x")', False))
mote_flow.ALLOWED_HOSTS = ["127.0.0.1"]                      # allow 127.0.0.1 only, NOT localhost
r = run('@get("http://127.0.0.1:8796/x")', False)
check("redirect to a host that is NOT allowed is not followed", hits["other"] == 0, f"other server was hit {hits['other']}x; {r}")
hits["other"] = 0
run('@const("a")|PT("http://127.0.0.1:8796/x")')
check("POST redirect to a host that is NOT allowed is not followed", hits["other"] == 0, f"other server was hit {hits['other']}x")
for url in ["file:///etc/passwd", "ftp://127.0.0.1/x", "http://127.0.0.1@localhost:8797/x", "http://localhost:8797/x", "http://127.0.0.1.evil.com/x", "//127.0.0.1/x", "gopher://127.0.0.1/x"]:
    hits["other"] = 0
    r = run(f'@get("{url}")', False)
    check("url refused: " + url, not r.get("ok") and hits["other"] == 0, r)
mote_flow.ALLOWED_HOSTS = []

# ---- 6. resource limits ----
for prog in ['@const("x")|A(y=9**9**9)', '@const("x")|SP(line,"")|C', '@dir("/")', '@dir(".")|C']:
    r = run(prog, False)
    check("does not hang or crash: " + prog, isinstance(r, dict))
r = run('@const("a")|A(y=num("10")**1000000)', False)
check("huge exponent refused", not r.get("ok"), r)
r = run('@const("a")|RX(line,"(a+)+$","m")', False)
check("regex step answers (known risk: patterns are agent-written)", isinstance(r, dict))


# ---- 6b. silent whole-number overflow must stop, not wrap around ----
import pandas as pd
import mote_fast
OV = {"t": pd.DataFrame({"k": ["x", "x"], "big": [9223372036854775807, 9223372036854775807], "i": [1, 2]})}
for prog in ["@t|A(y=10**30)", "@t|A(y=big+1)", "@t|A(y=big*2)", "@t|A(y=10**1000000)", "@t|G(k,S:big)", "@t|S(big)"]:
    try:
        mote_fast.run(prog, OV)
        check("overflow refused: " + prog, False)
    except mote.MoteError:
        check("overflow refused: " + prog, True)
check("normal whole-number maths still exact", mote_fast.run("@t|A(y=i*3+1)|S(y)", OV) == 11)
check("decimal maths still works near the limit", mote_fast.run("@t|A(y=big+1.0)|T(1)", OV)[0]["y"] > 9e18)

# ---- 7. MCP protocol abuse ----
import subprocess
import sys
p = subprocess.Popen([sys.executable, os.path.join(HERE, "mote_mcp.py"), "--data", data], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
msgs = ['not json', '[]', '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"mote_run","arguments":{"program":123}}}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mote_run","arguments":"x"}}',
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"../../etc","arguments":{}}}',
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"mote_restore","arguments":{"table":"x"}}}',
        '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"mote_run","arguments":{"program":"@tickets|C","apply":true}}}']
for m in msgs:
    p.stdin.write(m + "\n")
p.stdin.close()
out = p.stdout.read().splitlines()
p.wait(timeout=20)
check("server survives garbage and answers each valid request", len(out) >= 4 and p.returncode == 0, (len(out), p.returncode))
check("restore tool is not offered without --allow-writes", all('"isError": false' not in l for l in out if '"id": 4' in l or '"id":4' in l), out)

shutil.rmtree(tmp, ignore_errors=True)
print("\nALL PASS" if not bad else f"\n{bad} FAILED")
