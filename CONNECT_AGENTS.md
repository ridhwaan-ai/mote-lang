# Connect Mote to an AI agent (MCP)

Mote ships an MCP server, `mote_mcp.py`. Any agent that supports MCP tools can run Mote programs on your CSV tables.
It uses only Python's standard library for the server itself (the engine needs pandas and numpy).

## Safe by default
- **Read-only** unless you add `--allow-writes`.
- Even with `--allow-writes`, changes only PREVIEW until the agent sends `apply: true`.
- Every overwrite or delete keeps a backup; `mote_restore` (only offered with `--allow-writes`) puts it back.
- Every run is logged in `mote_log.jsonl`, tagged `mcp:<agent name>`.
- Start it on the sample data (`data`) first. Point it at real data only when you are ready.

## Claude Code (one command)
Replace `/path/to/mote-lang` with the folder you unzipped:
```
claude mcp add mote -- python /path/to/mote-lang/mote_mcp.py --data /path/to/mote-lang/data
```
Allow changes too (previews unless the agent asks to apply):
```
claude mcp add mote-rw -- python /path/to/mote-lang/mote_mcp.py --data /path/to/mote-lang/data --allow-writes
```
Remove it any time: `claude mcp remove mote`.

## Claude Desktop (claude_desktop_config.json)
```json
{
  "mcpServers": {
    "mote": {
      "command": "python",
      "args": ["/path/to/mote-lang/mote_mcp.py", "--data", "/path/to/mote-lang/data"]
    }
  }
}
```
(On Windows use full paths like `C:\path\to\mote-lang\mote_mcp.py`.)

## What the agent sees (about 335 estimated tokens of tool definitions)
- `mote_guide`: the compact language guide (about 670 estimated tokens). Call once.
- `mote_tables`: tables, row counts, fields.
- `mote_run`: run a program. Mistakes come back as plain fix-it messages.
- `mote_help`: details for one step or function.
- `mote_restore`: only with `--allow-writes`.

## Try it by hand (no agent needed)
```
python mote_run.py data "@tickets|C"
python mote_run.py data "@tickets|G(team,S:hours)|O(v)"
```
(`mote_run.py` takes the data folder first. Changes only preview unless you add `--apply`.)

## On a real database instead of CSV files (DuckDB or SQLite)
```
python mote_sqlapp.py import my.duckdb path/to/csv-folder
claude mcp add mote-db -- python /path/to/mote-lang/mote_mcp.py --db my.duckdb
```
Tested to 5 million rows. The read-only default is a Mote setting. For a hard guarantee, open the database with a read-only account.
