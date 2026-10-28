"""Talks to mote_mcp.py exactly like an MCP client: JSON-RPC over stdin/stdout, on a throwaway copy of the data."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import tokens as compare

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
data = os.path.join(tmp, "data")
shutil.copytree(os.path.join(HERE, "data"), data)
bad = 0


class Client:
    def __init__(self, *flags):
        self.p = subprocess.Popen([sys.executable, os.path.join(HERE, "mote_mcp.py"), "--data", data, *flags],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp)
        self.i = 0

    def send(self, method, params=None, notify=False):
        self.i += 1
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            msg["id"] = self.i
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        return None if notify else json.loads(self.p.stdout.readline())

    def tool(self, name, **args):
        r = self.send("tools/call", {"name": name, "arguments": args})["result"]
        return r["content"][0]["text"], r["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


def check(label, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + label + (f"   {extra}" if extra and not cond else ""))


c = Client()
r = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "testclient", "version": "1"}})
check("initialize returns server info and tools capability", r["result"]["serverInfo"]["name"] == "mote" and "tools" in r["result"]["capabilities"])
check("initialize echoes a supported protocol version", r["result"]["protocolVersion"] == "2025-06-18")
c.send("notifications/initialized", notify=True)
tools = c.send("tools/list")["result"]["tools"]
check("tools/list: 4 tools, no restore while writes are off", [t["name"] for t in tools] == ["mote_guide", "mote_tables", "mote_run", "mote_help"])
check("every tool has a name, description and schema", all(t["name"] and t["description"] and t["inputSchema"] for t in tools))
tool_tokens = compare.tok(json.dumps(tools))
print(f"     what an agent carries in its prompt for these tools: ~{tool_tokens} est. tokens")
check("tool definitions stay small (< 400 est. tokens)", tool_tokens < 400, str(tool_tokens))

g, e = c.tool("mote_guide")
check("mote_guide returns the compact guide", not e and "Mote guide" in g and compare.tok(g) < 800, str(compare.tok(g)))
t, e = c.tool("mote_tables")
check("mote_tables lists tables with fields", not e and "tickets" in t and "hours" in t)
t, e = c.tool("mote_run", program='@tickets|G(team,S:hours)|O(team)')
check("mote_run returns a result", not e and json.loads(t)["result"][0]["team"] == "bill", t)
t, e = c.tool("mote_run", program='@tickets|F(statu=="x")|C')
check("mote_run: a mistake comes back as a fix-it error (isError)", e and "unknown field 'statu'" in t, t)
t, e = c.tool("mote_run", program='@tickets|C', max_rows=1)
check("mote_run: single number result", not e and json.loads(t)["result"] == 12, t)
t, e = c.tool("mote_run", program='@tickets|P(id)', max_rows=3)
j = json.loads(t)
check("mote_run: long tables are cut and say so", j["rows"] == 12 and j["shown"] == 3 and len(j["result"]) == 3, t)
t, e = c.tool("mote_help", symbol="L")
check("mote_help returns details", not e and "one-to-one" in t)
t, e = c.tool("mote_run", program='I(x,a)', apply=True)
check("writes are refused when the server is read-only", e and "disabled" in t, t)
check("...and nothing was created", not os.path.exists(os.path.join(data, "x.csv")))
t, e = c.tool("mote_run", program='I(x,a)')
check("preview of a change still works read-only", not e and json.loads(t)["saved"] is False, t)
r = c.send("tools/call", {"name": "mote_restore", "arguments": {"table": "tickets"}})
check("mote_restore does not exist while read-only", "error" in r and r["error"]["code"] == -32602)
r = c.send("tools/call", {"name": "nope", "arguments": {}})
check("unknown tool gives a protocol error", "error" in r and r["error"]["code"] == -32602)
r = c.send("no/such/method")
check("unknown method gives a protocol error", r["error"]["code"] == -32601)
check("ping works", c.send("ping")["result"] == {})
c.p.stdin.write("this is not json\n"); c.p.stdin.flush()
check("bad JSON gets a parse error and the server survives", json.loads(c.p.stdout.readline())["error"]["code"] == -32700 and c.send("ping")["result"] == {})
t, e = c.tool("mote_run", program="")
check("empty program is refused politely", e)
c.close()

w = Client("--allow-writes")
w.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "agent2"}})
names = [t["name"] for t in w.send("tools/list")["result"]["tools"]]
check("with --allow-writes the restore tool appears", "mote_restore" in names)
t, e = w.tool("mote_run", program='I(todo,id,note,done)\n+todo(1,"a",0)\n+todo(2,"b",0)', apply=True)
check("apply=true creates and fills a table", not e and os.path.exists(os.path.join(data, "todo.csv")), t)
t, e = w.tool("mote_run", program='@todo|Q(id==2)|Z(todo,replace)')
check("an update/delete only previews unless apply=true", not e and json.loads(t)["saved"] is False and len(open(os.path.join(data, "todo.csv")).read().splitlines()) == 3, t)
t, e = w.tool("mote_run", program='@todo|Q(id==2)|Z(todo,replace)', apply=True)
check("apply=true saves it, keeping a backup", not e and len(open(os.path.join(data, "todo.csv")).read().splitlines()) == 2 and json.loads(t)["changes"][0]["backup"], t)
t, e = w.tool("mote_restore", table="todo")
check("mote_restore puts the backup back", not e and len(open(os.path.join(data, "todo.csv")).read().splitlines()) == 3, t)
w.close()
log = [json.loads(l) for l in open(os.path.join(HERE, "mote_log.jsonl"))][-12:]
check("runs are logged with who = the connecting client", any(str(e.get("who", "")).startswith("mcp:") for e in log))
print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAILED'}")
shutil.rmtree(tmp, ignore_errors=True)
