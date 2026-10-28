"""Mote v0.9: acting on the world. WF("file") writes a file, PT("url") posts JSON. Preview by default, backups, limits, atomic."""
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mote_app
import mote_flow

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
data = os.path.join(tmp, "data")
shutil.copytree(os.path.join(HERE, "data"), data)
mote_app.LOG = os.path.join(tmp, "log.jsonl")
store = mote_app.Store(data)
posts = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
        posts.append((self.path, self.headers.get("Content-Type"), body))
        code = 500 if self.path == "/fail" else 200
        self.send_response(code); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass


srv = ThreadingHTTPServer(("127.0.0.1", 8798), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
U = "http://127.0.0.1:8798"
mote_flow.ALLOWED_HOSTS = ["127.0.0.1"]
bad = 0


def run(prog, apply=False):
    return mote_app.run_program(prog, store, apply=apply, who="test")


def check(label, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + label + (f"   {extra}" if extra and not cond else ""))


def read(rel):
    p = os.path.join(data, rel)
    return open(p, encoding="utf-8", newline="").read() if os.path.exists(p) else None


T = '@tickets|G(team,S:hours)|O(team)'
# ---- files ----
r = run(T + '|WF("reports/teams.md")')
check("preview: a file write is reported but nothing is written", r["ok"] and r["changes"][0]["op"] == "write file" and r["saved"] is False and read("reports/teams.md") is None, str(r))
r = run(T + '|WF("reports/teams.md")', apply=True)
check("apply: the markdown file is written exactly", r["ok"] and r["saved"] and read("reports/teams.md") == "| team | v |\n|---|---|\n| bill | 16.0 |\n| sup | 32.0 |", str(read("reports/teams.md")))
r = run('@tickets|G(team,C=n)|O(team)|WF("reports/teams.md")', apply=True)
check("overwriting makes a backup of the old file", r["ok"] and r["changes"][0]["backup"] and os.path.exists(os.path.join(data, "_backups", r["changes"][0]["backup"])), str(r))
check("...and the new content is in place", read("reports/teams.md") == "| team | n |\n|---|---|\n| bill | 6 |\n| sup | 6 |")
run('@tickets|P(id,team)|T(2)|WF("reports/t.csv")', apply=True)
check("csv: header and rows, newline endings", read("reports/t.csv") == "id,team\n1,sup\n2,sup\n", repr(read("reports/t.csv")))
run('@tickets|P(id,team)|T(2)|WF("reports/t.json")', apply=True)
check("json: a list of records", json.loads(read("reports/t.json")) == [{"id": 1, "team": "sup"}, {"id": 2, "team": "sup"}])
run('@tickets|P(team)|U(team)|TX(team)|WF("reports/note.txt")', apply=True)
check("txt: a single text value", read("reports/note.txt") == "sup\nbill", repr(read("reports/note.txt")))
run('@tickets|C|WF("reports/count.json")', apply=True)
check("a single number can be written as json", read("reports/count.json") == "12")
for label, prog in [("a table into .txt", T + '|WF("x.txt")'), ("a number into .csv", '@tickets|C|WF("x.csv")'), ("an unknown file type", T + '|WF("x.dat")'),
                    ("an executable type (.py)", T + '|WF("evil.py")'), ("a script type (.bat)", T + '|WF("evil.bat")'), ("a path outside the data folder", T + '|WF("../x.csv")'),
                    ("an absolute path", T + '|WF("C:/Windows/x.csv")'), ("the backups folder", T + '|WF("_backups/x.csv")'), ("an unquoted name", T + '|WF(x.csv)')]:
    r = run(prog, apply=True)
    check(f"refused: {label}", not r["ok"], str(r)[:120])
check("none of the refused writes left a file", read("x.txt") is None and read("x.csv") is None and read("evil.py") is None and read("evil.bat") is None and not os.path.exists(os.path.join(tmp, "x.csv")))
r = run(T + '|WF("reports/never.md")\n@tickets|A(x=nope)', apply=True)
check("an error later in the program means NO file is written (atomic)", not r["ok"] and read("reports/never.md") is None, str(r)[:100])

# ---- posts ----
r = run(T + f'|PT("{U}/ok")')
check("preview: nothing is posted", r["ok"] and r["changes"][0]["op"] == "post" and not posts, str(r))
r = run(T + f'|PT("{U}/ok")', apply=True)
check("apply: the table is posted as JSON", r["ok"] and len(posts) == 1 and posts[0][1] == "application/json" and json.loads(posts[0][2]) == [{"team": "bill", "v": 16.0}, {"team": "sup", "v": 32.0}], str((r, posts)))
r = run(T + f'|PT("{U}/fail")', apply=True)
check("a server error stops the run with a clear message", not r["ok"] and "HTTP 500" in r["error"], str(r)[:120])
r = run(T + '|PT("http://example.com/x")')
check("a host that is not allowed is refused even in preview", not r["ok"] and "not allowed" in r["error"], str(r)[:120])
r = run(T + '|PT("file:///etc/x")')
check("a non-web address is refused", not r["ok"])
r = run(f'@tickets|C|PT("{U}/ok")', apply=True)
check("a single value can be posted", r["ok"] and json.loads(posts[-1][2]) == 12, str(posts[-1:]))
posts.clear()
prog = "\n".join(T + f'|PT("{U}/ok")' for _ in range(6))
r = run(prog, apply=True)
check("more than 5 posts in one program is refused and sends none", not r["ok"] and not posts, str((r, posts))[:140])
r = run(T + '|WF("reports/kept.md")\n' + T + f'|PT("{U}/fail")', apply=True)
check("if a post fails after a file was written, the message says what was kept", not r["ok"] and "kept" in r["error"] and read("reports/kept.md") is not None, str(r)[:200])
log = [json.loads(l) for l in open(mote_app.LOG)]
check("the log records file and post changes", any(any(c["op"] == "post" for c in e.get("writes", [])) for e in log) and any(any(c["op"] == "write file" for c in e.get("writes", [])) for e in log))
# tables still behave exactly as before alongside the new effects
r = run('I(todo,id,note)\n+todo(1,"a")\n@todo|WF("reports/todo.csv")', apply=True)
check("tables and files can be changed in the same run", r["ok"] and read("todo.csv") is not None and read("reports/todo.csv") == "id,note\n1,a\n", str(r)[:160])
print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAILED'}")
srv.shutdown()
