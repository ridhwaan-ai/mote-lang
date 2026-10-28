"""Tiny MCP client for trials. Usage:  python mcp_call.py --list   |   python mcp_call.py TOOL '{"arg": 1}'
Starts the Mote MCP server for one call, speaks JSON-RPC over stdio, prints the result, and logs the call."""
import json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("MOTE_DATA", os.path.join(HERE, "data_real"))

def talk(messages):
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "mote_mcp.py"), "--data", DATA], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=HERE)
    out = []
    for m in messages:
        p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
        if "id" in m: out.append(json.loads(p.stdout.readline()))
    p.stdin.close(); p.wait(timeout=30)
    return out

INIT = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "trial-agent"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"}]
if __name__ == "__main__":
    if sys.argv[1] == "--list":
        r = talk(INIT + [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])[1]["result"]["tools"]
        print(json.dumps(r, indent=1)); name, args = "--list", {}
    else:
        name, args = sys.argv[1], (json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
        r = talk(INIT + [{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}])[1]
        if "error" in r: print("PROTOCOL ERROR:", r["error"]["message"]); is_err = True
        else:
            res = r["result"]; print(("TOOL ERROR: " if res["isError"] else "") + res["content"][0]["text"]); is_err = res["isError"]
    with open(os.path.join(HERE, "mcp_calls.jsonl"), "a") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "tool": name, "args": args}) + "\n")
