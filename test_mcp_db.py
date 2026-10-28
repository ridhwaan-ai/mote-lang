"""The MCP plug-in running on a real DuckDB FILE (--db). Same protocol checks as test_mcp.py plus persistence."""
import json
import os
import subprocess
import sys
import tempfile

import duckdb

import mote_sqlapp

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "test.duckdb")
print("imported:", mote_sqlapp.import_csvs(db, os.path.join(HERE, "data")))
bad = 0


class Client:
    def __init__(self, *flags):
        self.p = subprocess.Popen([sys.executable, os.path.join(HERE, "mote_mcp.py"), "--db", db, *flags], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp)
        self.i = 0
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "dbagent"}})

    def send(self, method, params=None):
        self.i += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, **args):
        r = self.send("tools/call", {"name": name, "arguments": args})["result"]
        return r["content"][0]["text"], r["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=20)


def check(label, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + label + (f"   {extra}" if extra and not cond else ""))


c = Client()
t, e = c.tool("mote_tables")
check("mote_tables lists the database tables", not e and "tickets" in t and "agents" in t, t)
t, e = c.tool("mote_run", program='@tickets|G(team,S:hours)|O(team)')
check("mote_run answers from the database", not e and json.loads(t)["result"][0]["team"] == "bill", t)
t, e = c.tool("mote_run", program='@tickets|F(statu=="x")|C')
check("a mistake comes back as a fix-it error", e and "unknown field 'statu'" in t, t)
t, e = c.tool("mote_run", program='I(x,a)', apply=True)
check("writes refused while read-only", e and "disabled" in t)
t, e = c.tool("mote_run", program='@tickets|A(w=weekday(opened))|G(w,C)|O(w)')
check("a date function runs on DuckDB", not e and json.loads(t)["rows"] >= 1, t)
c.close()

w = Client("--allow-writes")
t, e = w.tool("mote_run", program='I(todo,id,note,done)\n+todo(1,"a",0)\n+todo(2,"b",0)')
check("preview of create+insert reports changes but does not save", not e and json.loads(t)["saved"] is False, t)
t, e = w.tool("mote_run", program='@todo|C')
check("...and the table does not exist yet", e and "no such" in t.lower() or "todo" in t, t)
t, e = w.tool("mote_run", program='I(todo,id,note,done)\n+todo(1,"a",0)\n+todo(2,"b",0)', apply=True)
check("apply=true creates and fills the table", not e and json.loads(t)["saved"] is True, t)
t, e = w.tool("mote_run", program='@todo|Q(id==2)|Z(todo,replace)', apply=True)
check("delete + save with a backup", not e and json.loads(t)["changes"][0]["backup"], t)
t, e = w.tool("mote_restore", table="todo")
check("mote_restore brings the backup back", not e, t)
t, e = w.tool("mote_run", program='@todo|C')
check("...2 rows again", not e and json.loads(t)["result"] == 2, t)
w.close()
con = duckdb.connect(db)
n = con.execute("SELECT COUNT(*) FROM todo").fetchone()[0]
con.close()
check("the changes are really in the database file after the server stops", n == 2, str(n))
print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAILED'}")
