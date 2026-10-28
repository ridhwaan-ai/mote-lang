# How to try Mote yourself

Needs Python 3.10+ and `pip install pandas numpy` (add `duckdb` for the database mode).

## 1. Open the app
```
python mote_app.py serve --data data
```
A page opens at http://127.0.0.1:8765 on the included sample data (`data/`: tickets, agents, esc). Stop it with Ctrl+C.

## 2. Try these (copy into the Program box, press Run)
Read:
- `@tickets|C`                              how many rows
- `@tickets|G(team,S:hours)|O(v)`           hours per team
- `@tickets|F(prio==1)|C`                   count of priority-1 tickets
- `@tickets|F(status~"clo")|C`              text search, any capitalisation
- `@tickets|SD(hours)`                      how spread out the hours are

Change data (these only PREVIEW until you tick "Save changes to disk"):
- `I(shopping,id,item,bought)`              create a table
- `+shopping(1,"milk",0)`                   add a row
- `@shopping|Y(bought=1,id==1)|Z(shopping,replace)`   update a row
- `@shopping|Q(bought==1)|Z(shopping,replace)`        delete rows
- `-shopping`                               delete the table

Make Mote refuse (this is the point of it):
- `@tickets|A(x=hours+team)|C`              text in maths, it stops and tells you how to fix it

Undo a mistake: every overwrite or delete keeps a copy in `data/_backups`.
From a terminal: `python mote_app.py restore shopping --data data`

## 3. Let an agent use it
See `CONNECT_AGENTS.md`. Every run is logged in `mote_log.jsonl` (time, who ran it, program, changes).

## Automated tests
Run any `test_*.py` file, for example `python test_write.py` or `python test_recon.py`.

## What Mote cannot do yet (be honest)
No users or passwords, no automatic scheduling, no Postgres yet, and no independent security review. It is an early prototype.
