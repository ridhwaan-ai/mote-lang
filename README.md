# Mote (mote-lang)

A small, checked pipeline language that AI agents use to work with tables, files and web requests.
One short line instead of a page of Python or SQL, and every change is previewed before it is saved.

```
@orders|F(status="paid")|G(country;total=S(amount))|O(-total)|T(3)
```

## Why it exists
Agents burn tokens (and make mistakes) writing long pandas/SQL code for routine data jobs.
Mote is short, has clear error messages that tell the agent how to fix the line, and refuses unsafe things by default.

## Safety (what is built and tested)
- **Read-only by default.** The MCP server cannot change anything unless started with `--allow-writes`.
- **Preview first.** Every change shows what it would do; nothing is saved until `apply`.
- **Backups.** Every applied change to a table or file keeps a restorable backup (`mote_restore`).
- **Fenced effects.** Writing files is limited to safe extensions inside the data folder; web calls only go to hosts you list in `allowed_hosts.txt` (none by default); post count and size limits.
- **No `eval`/`exec`.** Expressions are parsed into a syntax tree and only numbers, field names, + - * / % ** and brackets are evaluated. (An earlier build used a fenced `eval` in the reference engine; it was replaced with the tree evaluator in 0.1.1.)
- **Injection tests.** 33 automated checks (hostile strings stored as plain data, no escaped writes).
- **Attack tests (`test_attacks.py`).** Path traversal on read and write, blocked file types (including Windows tricks like `a.py.`), bad table names, web redirects to hosts you did not allow (not followed), odd URL schemes, whole-number overflow (stops with an error instead of wrapping to a wrong number), and garbage sent to the MCP server. Two real bugs were found and fixed this way in 0.1.2.
- **Audit log.** Every run is logged with who ran it.

Not claimed: "safer than SQL" has NOT been proven, and there has been no independent human security review.

## Measured results (small, honest)
- Blind weaker-model tests (Haiku) solved the data jobs correctly, including a 4-pass bank-to-invoice matcher (15/15).
- About 15% fewer tokens than SQL on ordinary jobs; about 3.6x fewer than Python on reconciliation (rough token proxy in `tokens.py`, not a real tokenizer).
- DuckDB engine: 5 million rows in 0.1-0.2 s.

## Try it
```
pip install pandas numpy duckdb
python mote_app.py serve --data data
python test_write.py     # and the other test_*.py files
```
Connect an agent: see `CONNECT_AGENTS.md`. Language reference: `GUIDE_FULL.md` (compact: `GUIDE_COMPACT.md`).

## Status
Early prototype. Postgres not done. Needs real-world users.

## Licence
Free for personal use, learning, research and testing under the PolyForm Noncommercial License 1.0.0 (see `LICENSE`).
**Any commercial use** (a business, a paid product or service, an agent that earns money) needs the paid commercial licence, sold with the packs and guides:

- Agensi (agent skill): https://www.agensi.io/skills/mote
- Gumroad: https://saibridhwaan.gumroad.com/l/ajwzag
