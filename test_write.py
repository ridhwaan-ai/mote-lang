"""v0.5 write path: create / insert / update / delete / save / drop / restore, preview-vs-apply, backups, atomicity.
Runs on a throwaway copy of data/ - the real files are never touched."""
import hashlib
import json
import os
import shutil
import tempfile

import pandas as pd

import mote_app

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
data = os.path.join(tmp, "data")
shutil.copytree(os.path.join(HERE, "data"), data)
mote_app.LOG = os.path.join(tmp, "log.jsonl")
store = mote_app.Store(data)
bad = 0


def sha(name):
    p = os.path.join(data, name + ".csv")
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


def run(prog, apply=False, who="test"):
    return mote_app.run_program(prog, store, apply=apply, who=who)


def check(label, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + label + (f"   {extra}" if extra and not cond else ""))


def rows(name):
    return pd.read_csv(os.path.join(data, name + ".csv")).to_dict("records")


# 1. preview never touches disk
before = {n: sha(n) for n in ("tickets", "agents", "esc")}
r = run('I(notes,id,text,done)')
check("create table: preview ok", r["ok"] and r.get("saved") is False and r["changes"][0]["op"] == "create")
check("create table: preview leaves disk untouched", not os.path.exists(os.path.join(data, "notes.csv")))
r = run('I(notes,id,text,done)', apply=True)
check("create table: apply writes the file", r["ok"] and r["saved"] and os.path.exists(os.path.join(data, "notes.csv")))
check("create table: creating again is refused", not run('I(notes,id,text,done)', apply=True)["ok"])

# 2. insert
r = run('+notes(1,"call bank",0)\n+notes(2,"send invoice",0)\n+notes(3,"file taxes",1)', apply=True)
check("insert 3 rows (one run, one write)", r["ok"] and r["changes"][0]["rows_after"] == 3 and len(rows("notes")) == 3, str(r))
check("insert: wrong value count refused", not run('+notes(4,"x")', apply=True)["ok"])
check("insert: text into a number field refused", not run('+notes("abc","x",0)', apply=True)["ok"])
check("insert: unknown table refused", not run('+nope(1)', apply=True)["ok"])

# 3. update (only changes what matches; nothing saved without Z)
r = run('@notes|Y(done=1,id==1)')
check("update without Z changes nothing on disk", r["ok"] and not r.get("changes") and rows("notes")[0]["done"] == 0)
r = run('@notes|Y(done=1,id==1)|Z(notes,replace)', apply=True)
n = rows("notes")
check("update one row, save, others untouched", r["ok"] and [x["done"] for x in n] == [1, 0, 1], str(n))
check("update made a backup", bool(r["changes"][0]["backup"]) and os.path.exists(os.path.join(store.backups, r["changes"][0]["backup"])))
r = run('@notes|Y(text=upper(text))|Z(notes,replace)', apply=True)
check("update all rows with a text function", [x["text"] for x in rows("notes")] == ["CALL BANK", "SEND INVOICE", "FILE TAXES"])
r = run('@tickets|Y(hours=hours*2,status=="open")|Z(tickets,replace)', apply=True)
t = {x["id"]: x["hours"] for x in rows("tickets")}
check("update with maths on matching rows only", t[3] == 2.0 and t[5] == 16.0 and t[1] == 2.0, str(t))
check("update: text into number field refused", not run('@tickets|Y(hours="abc")|Z(tickets,replace)', apply=True)["ok"])
check("update: unknown field refused", not run('@tickets|Y(nope=1)', apply=True)["ok"])

# 4. delete
r = run('@notes|Q(done==1)|Z(notes,replace)', apply=True)
check("delete rows by condition", r["ok"] and [x["id"] for x in rows("notes")] == [2], str(rows("notes")))

# 5. save a query as a new table, add, refuse overwrite
r = run('@tickets|F(status=="open")|Z(open_tickets)', apply=True)
check("save query result as new table", r["ok"] and len(rows("open_tickets")) == 4)
check("saving over an existing table is refused", not run('@tickets|F(status=="open")|Z(open_tickets)', apply=True)["ok"])
r = run('@tickets|F(status=="closed"&prio==1)|Z(open_tickets,add)', apply=True)
check("append rows with Z(name,add)", r["ok"] and len(rows("open_tickets")) == 4 + 3, str(r))
check("append with different fields refused", not run('@tickets|P(id)|Z(open_tickets,add)', apply=True)["ok"])

# 6. drop and restore
r = run('-open_tickets', apply=True)
check("drop table", r["ok"] and not os.path.exists(os.path.join(data, "open_tickets.csv")))
check("drop table made a backup", bool(r["changes"][0]["backup"]))
store.restore("open_tickets")
check("restore brings it back", len(rows("open_tickets")) == 7)
check("create then drop in one run changes nothing", run('I(tmpx,a)\n-tmpx', apply=True).get("changes") in (None, []) and not os.path.exists(os.path.join(data, "tmpx.csv")))

# 7. atomic: an error anywhere means nothing is written
r = run('@tickets|Z(tmp1)\n@tickets|A(x=nope)', apply=True)
check("error later in the program writes nothing", not r["ok"] and not os.path.exists(os.path.join(data, "tmp1.csv")))

# 8. original data untouched by preview-only runs, log records who/apply/writes
check("untouched tables still identical", sha("agents") == before["agents"] and sha("esc") == before["esc"])
log = [json.loads(l) for l in open(mote_app.LOG)]
check("log records who and apply", all("who" in e and "apply" in e for e in log) and any(e.get("writes") for e in log))
check("log keeps failed runs", any(not e["ok"] for e in log))

print(f"\n{sum(1 for _ in log)} runs logged. {'ALL PASS' if not bad else str(bad) + ' FAILED'}")
shutil.rmtree(tmp, ignore_errors=True)
