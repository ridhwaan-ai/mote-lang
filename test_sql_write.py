"""Write path on a real SQL database (SQLite): create / insert / update / delete / save / drop / restore,
preview = rollback, apply = commit, backups, atomic errors. Same scenarios as test_write.py (the CSV version)."""
import os
import sqlite3
import tempfile

import pandas as pd

import mote
import mote_sql
import dbtools

HERE = os.path.dirname(os.path.abspath(__file__))
con = dbtools.connect()
for n in ("tickets", "agents", "esc"):
    dbtools.load(con, n, pd.read_csv(os.path.join(HERE, "data", n + ".csv")))
S = mote_sql.Session(con)
bad = 0


def run(prog, apply=False):
    try:
        res, ch = S.run(prog, apply=apply)
        return {"ok": True, "result": res, "changes": ch, "saved": apply and bool(ch)}
    except mote.MoteError as e:
        return {"ok": False, "error": str(e)}


def rows(name):
    cur = con.execute(f'SELECT * FROM "{name}" ORDER BY rowid')
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def exists(name):
    return name in S.d.tables(con, include_backups=True)


def check(label, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + label + (f"   {extra}" if extra and not cond else ""))


before = {n: rows(n) for n in ("tickets", "agents", "esc")}
r = run('I(notes,id,text,done)')
check("create: preview ok", r["ok"] and r["changes"][0]["op"] == "create" and not r["saved"])
check("create: preview leaves the database untouched", not exists("notes"))
r = run('I(notes,id,text,done)', apply=True)
check("create: apply writes it", r["ok"] and r["saved"] and exists("notes"))
check("create: again is refused", not run('I(notes,id,text,done)', apply=True)["ok"])

r = run('+notes(1,"call bank",0)\n+notes(2,"send invoice",0)\n+notes(3,"file taxes",1)', apply=True)
check("insert 3 rows, reported as one change", r["ok"] and r["changes"][0]["rows_after"] == 3 and len(rows("notes")) == 3, str(r))
check("insert: wrong value count refused", not run('+notes(4,"x")', apply=True)["ok"])
check("insert: text into a number field refused", not run('+notes("abc","x",0)', apply=True)["ok"])
check("insert: unknown table refused", not run('+nope(1)', apply=True)["ok"])
check("failed inserts changed nothing", len(rows("notes")) == 3)

r = run('@notes|Y(done=1,id==1)')
check("update without Z changes nothing", r["ok"] and not r["changes"] and rows("notes")[0]["done"] == 0)
r = run('@notes|Y(done=1,id==1)|Z(notes,replace)', apply=True)
check("update one row, save, others untouched", r["ok"] and [x["done"] for x in rows("notes")] == [1, 0, 1], str(rows("notes")))
check("update made a backup table", bool(r["changes"][0]["backup"]) and exists(r["changes"][0]["backup"]))
run('@notes|Y(text=upper(text))|Z(notes,replace)', apply=True)
check("update all rows with a text function", [x["text"] for x in rows("notes")] == ["CALL BANK", "SEND INVOICE", "FILE TAXES"])
run('@tickets|Y(hours=hours*2,status=="open")|Z(tickets,replace)', apply=True)
t = {x["id"]: x["hours"] for x in rows("tickets")}
check("update with maths on matching rows only", t[3] == 2.0 and t[5] == 16.0 and t[1] == 2.0, str(t))
check("update: text into number field refused", not run('@tickets|Y(hours="abc")|Z(tickets,replace)', apply=True)["ok"])
check("update: unknown field refused", not run('@tickets|Y(nope=1)', apply=True)["ok"])
check("failed updates changed nothing", {x["id"]: x["hours"] for x in rows("tickets")} == t)

r = run('@notes|Q(done==1)|Z(notes,replace)', apply=True)
check("delete rows by condition", r["ok"] and [x["id"] for x in rows("notes")] == [2], str(rows("notes")))

r = run('@tickets|F(status=="open")|Z(open_tickets)', apply=True)
check("save a query as a new table", r["ok"] and len(rows("open_tickets")) == 4)
check("saving over an existing table is refused", not run('@tickets|F(status=="open")|Z(open_tickets)', apply=True)["ok"])
r = run('@tickets|F(status=="closed"&prio==1)|Z(open_tickets,add)', apply=True)
check("append rows with Z(name,add)", r["ok"] and len(rows("open_tickets")) == 7, str(r))
check("append with different fields refused", not run('@tickets|P(id)|Z(open_tickets,add)', apply=True)["ok"])

r = run('-open_tickets', apply=True)
check("drop table", r["ok"] and not exists("open_tickets"))
check("drop made a backup", bool(r["changes"][0]["backup"]))
S.restore("open_tickets")
check("restore brings it back", exists("open_tickets") and len(rows("open_tickets")) == 7)
r = run('I(tmpx,a)\n-tmpx', apply=True)
check("create then drop in one run changes nothing", r["ok"] and not r["changes"] and not exists("tmpx"))

r = run('@tickets|Z(tmp1)\n@tickets|A(x=nope)', apply=True)
check("an error later in the program rolls everything back", not r["ok"] and not exists("tmp1"))
r = run('I(tmp2,a)\n+tmp2(1)\n@tickets|A(x=hours/0)', apply=True)
check("...also for creates and inserts before the error", not r["ok"] and not exists("tmp2"))

# a saved line feeds a later line
r = run('x=@tickets|F(status=="open")|P(id)\n@x|C')
check("a saved line can be used by the next line", r["ok"] and r["result"] == 4, str(r))
check("untouched tables identical", rows("agents") == before["agents"] and rows("esc") == before["esc"])
print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAILED'}")
