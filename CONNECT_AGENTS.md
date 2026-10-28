# Connect Mote to an AI agent (MCP)

Mote ships an MCP server, `mote_mcp.py`. Any agent that supports MCP tools can run Mote programs on your CSV tables.
It uses only Python's standard library. Nothing to install.

## Safe by default
- **Read-only** unless you add `--allow-writes`.
- Even with `--allow-writes`, changes only PREVIEW until the agent sends `apply: true`.
- Every overwrite or delete keeps a backup; `mote_restore` (only offered with `--allow-writes`) puts it back.
- Every run is logged in `mote_log.jsonl`, tagged `mcp:<agent name>`.
- Start it on the SANDBOX data (`data_demo`) first. Point it at real data only when you are ready.

## Claude Code (one command, run it yourself)
```
claude mcp add mote -- C:\Python314\python.exe C:\Users\User\Desktop\claude-tv\ai-lang\mote_mcp.py --data C:\Users\User\Desktop\claude-tv\ai-lang\data_demo
```
Allow changes too (previews unless the agent asks to apply):
```
claude mcp add mote-rw -- C:\Python314\python.exe C:\Users\User\Desktop\claude-tv\ai-lang\mote_mcp.py --data C:\Users\User\Desktop\claude-tv\ai-lang\data_demo --allow-writes
```
Remove it any time: `claude mcp remove mote`.

## Claude Desktop (claude_desktop_config.json)
```json
{
  "mcpServers": {
    "mote": {
      "command": "C:\\Python314\\python.exe",
      "args": ["C:\\Users\\User\\Desktop\\claude-tv\\ai-lang\\mote_mcp.py", "--data", "C:\\Users\\User\\Desktop\\claude-tv\\ai-lang\\data_demo"]
    }
  }
}
```

## What the agent sees (about 335 estimated tokens of tool definitions)
- `mote_guide`: the 670-token language guide. Call once.
- `mote_tables`: tables, row counts, fields.
- `mote_run`: run a program. Mistakes come back as plain fix-it messages.
- `mote_help`: details for one step or function.
- `mote_restore`: only with `--allow-writes`.

## Try it by hand (no agent needed)
```
cd C:\Users\User\Desktop\claude-tv\ai-lang
C:\Python314\python.exe mcp_call.py --list
C:\Python314\python.exe mcp_call.py mote_run "{\"program\": \"@history|C\"}"
```
(`mcp_call.py` reads `data_real`; set the MOTE_DATA environment variable to use another folder.)

## Tests
`C:\Python314\python.exe test_mcp.py`  (28 checks: protocol, safety, backups, logging)

## On a real database instead of CSV files (DuckDB or SQLite)
```
C:\Python314\python.exe mote_sqlapp.py import C:\path\to\my.duckdb C:\path\to\csv-folder
claude mcp add mote-db -- C:\Python314\python.exe C:\Users\User\Desktop\claude-tv\ai-lang\mote_mcp.py --db C:\path\to\my.duckdb
```
Tested to 5 million rows. The read-only default is an Mote setting. For a hard guarantee, open the database with a read-only account.
