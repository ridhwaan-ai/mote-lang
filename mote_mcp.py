"""Mote as an MCP server (stdio): lets any AI agent run checked Mote programs on CSV tables.

  python mote_mcp.py --data <folder of CSVs> [--allow-writes]
  python mote_mcp.py --db <file.duckdb | file.db> [--allow-writes]      run on a real database instead of CSV files

Tools: mote_guide (read first), mote_tables, mote_run, mote_help, and mote_restore (only with --allow-writes).
Writes are OFF unless the server is started with --allow-writes; even then mote_run previews unless apply=true.
Standard library only (no downloads). Protocol: JSON-RPC 2.0, one message per line.
"""
import json
import os
import sys

import mote_app
import mote_sqlapp

HERE = os.path.dirname(os.path.abspath(__file__))
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_ROWS = 50

TOOLS = [
    {"name": "mote_guide",
     "description": "Mote: short data-pipeline language for CSV tables. Call once first to learn it.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mote_tables",
     "description": "List tables: name, rows, fields.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mote_run",
     "description": "Run an Mote program, e.g. @sales|F(paid==1)|S(amt). Changes only preview unless apply=true.",
     "inputSchema": {"type": "object", "required": ["program"], "properties": {
         "program": {"type": "string"}, "apply": {"type": "boolean"}, "max_rows": {"type": "integer"}}}},
    {"name": "mote_help",
     "description": "Details for one Mote step or function, e.g. L, Y, days.",
     "inputSchema": {"type": "object", "required": ["symbol"], "properties": {"symbol": {"type": "string"}}}},
]
RESTORE = {"name": "mote_restore",
           "description": "Restore the newest backup of a table.",
           "inputSchema": {"type": "object", "required": ["table"], "properties": {"table": {"type": "string"}}}}


class Server:
    def __init__(self, data, allow_writes, db=None):
        self.store = mote_sqlapp.SqlStore(os.path.abspath(db)) if db else mote_app.Store(os.path.abspath(data))
        self.db = bool(db)
        self.allow_writes = allow_writes
        self.client = "mcp"

    def tools(self):
        return TOOLS + ([RESTORE] if self.allow_writes else [])

    def guide(self):
        with open(os.path.join(HERE, "GUIDE_COMPACT.md"), encoding="utf-8") as f:
            return f.read()

    def call(self, name, args):
        """Returns (text, is_error)."""
        if name == "mote_guide":
            return self.guide(), False
        if name == "mote_tables":
            return json.dumps(self.store.describe() if self.db else mote_app.describe(self.store), separators=(",", ":")), False
        if name == "mote_help":
            return mote_app.help_text(str(args.get("symbol", ""))), False
        if name == "mote_restore":
            if not self.allow_writes:
                return "Writes are disabled on this server.", True
            try:
                return "restored from " + self.store.restore(str(args.get("table", ""))), False
            except Exception as e:
                return str(e), True
        if name == "mote_run":
            prog = args.get("program")
            if not isinstance(prog, str) or not prog.strip():
                return "program must be a non-empty string", True
            apply = bool(args.get("apply"))
            if apply and not self.allow_writes:
                return "Writes are disabled on this server (it was started without --allow-writes). Run without apply to preview.", True
            r = (self.store.run_program(prog, apply=apply, who=self.client) if self.db
                 else mote_app.run_program(prog, self.store, apply=apply, who=self.client))
            if not r["ok"]:
                return r["error"], True
            res, out = r["result"], {}
            if isinstance(res, list):
                limit = max(1, min(int(args.get("max_rows") or MAX_ROWS), 500))
                out["rows"] = len(res)
                if len(res) > limit:
                    out["shown"] = limit
                res = res[:limit]
            out["result"] = res
            if r.get("changes"):
                out["changes"] = r["changes"]
                out["saved"] = r["saved"]
            return json.dumps(out, separators=(",", ":"), default=str), False
        return f"unknown tool {name}", True

    def handle(self, msg):
        """One JSON-RPC message in, response dict (or None for notifications) out."""
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            self.client = "mcp:" + str((params.get("clientInfo") or {}).get("name", "agent"))[:15]
            want = params.get("protocolVersion")
            return self.ok(mid, {"protocolVersion": want if want in SUPPORTED else SUPPORTED[-1],
                                 "capabilities": {"tools": {}},
                                 "serverInfo": {"name": "mote", "version": "0.5"},
                                 "instructions": "Mote runs short, checked data pipelines on CSV tables. Call mote_guide first."})
        if method in ("notifications/initialized", "notifications/cancelled") or (method or "").startswith("notifications/"):
            return None
        if method == "ping":
            return self.ok(mid, {})
        if method == "tools/list":
            return self.ok(mid, {"tools": self.tools()})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t["name"] for t in self.tools()}:
                return self.err(mid, -32602, f"Unknown tool: {name}")
            try:
                text, is_err = self.call(name, args)
            except Exception as e:   # never crash the server on a bad call
                text, is_err = f"internal error {type(e).__name__}: {e}", True
            return self.ok(mid, {"content": [{"type": "text", "text": text}], "isError": is_err})
        if mid is None:
            return None
        return self.err(mid, -32601, f"Method not found: {method}")

    @staticmethod
    def ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def err(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(server):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(server.err(None, -32700, "Parse error")) + "\n")
            sys.stdout.flush()
            continue
        out = server.handle(msg) if isinstance(msg, dict) else server.err(None, -32600, "Invalid request")
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    a = sys.argv
    data = a[a.index("--data") + 1] if "--data" in a else os.path.join(HERE, "data")
    serve(Server(data, "--allow-writes" in a, db=a[a.index("--db") + 1] if "--db" in a else None))
