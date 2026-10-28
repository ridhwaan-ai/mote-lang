"""Attack tests: program text comes from agents, so try to make the generated SQL do something it should not.
Every attempt must either be stored/compared as plain text or be refused. Tables and secrets must stay untouched.
Run on both databases:  python test_injection.py   and   MOTE_DB=duckdb python test_injection.py"""
import pandas as pd

import mote
import mote_sql
import dbtools

con = dbtools.connect()
dbtools.load(con, "t", pd.DataFrame({"id": [1, 2, 3], "name": ["alice", "bob%", "o'neil"], "amt": [10.0, 20.0, 30.0]}))
dbtools.load(con, "secrets", pd.DataFrame({"k": ["pw"], "v": ["hunter2"]}))
S = mote_sql.Session(con)
bad = 0


def run(prog, apply=False):
    try:
        return "ok", S.run(prog, apply=apply)[0]
    except mote.MoteError as e:
        return "refused", str(e)
    except mote_sql.Unsupported as e:
        return "refused", "unsupported " + str(e)
    except Exception as e:                       # a database error would mean the text reached the database unchecked
        return "DB ERROR", f"{type(e).__name__}: {e}"


def check(label, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + label + (f"   {extra}" if extra and not cond else ""))


def intact(label):
    tabs = set(S.names())
    n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    s = con.execute("SELECT v FROM secrets").fetchall()
    check(f"{label}: tables, rows and secrets untouched", tabs == {"t", "secrets"} and n == 3 and s == [("hunter2",)], f"{tabs} {n} {s}")


# --- quotes and SQL inside TEXT values are just text ---
st, r = run('@t|F(name=="x\' OR \'1\'=\'1")|C')
check("quote-and-OR in a comparison value is plain text", st == "ok" and r == 0, str((st, r)))
st, r = run('@t|F(name=="\'; DROP TABLE t; --")|C')
check("DROP TABLE inside a comparison value is plain text", st == "ok" and r == 0, str((st, r)))
st, r = run("@t|F(name==\"o'neil\")|C")
check("a real apostrophe in a value still matches", st == "ok" and r == 1, str((st, r)))
st, r = run('@t|F(name~"%")|C')
check("LIKE wildcard % is matched as a literal character", st == "ok" and r == 1, str((st, r)))
st, r = run('@t|F(name~"_")|C')
check("LIKE wildcard _ is matched as a literal character", st == "ok" and r == 0, str((st, r)))
intact("text-value attacks")

# --- insert and update with hostile text (apply=True really writes) ---
payload = "'); DROP TABLE t; --"
st, r = run(f'+t(99,"{payload}",1)', apply=True)
check("insert of a hostile string succeeds as data", st == "ok", str((st, r)))
row = con.execute("SELECT name FROM t WHERE id = 99").fetchall()
check("...and is stored exactly as typed", row == [(payload,)], str(row))
check("...and the table still exists", "t" in S.names())
st, r = run("@t|Y(name=\"x'); DROP TABLE t; --\",id==99)|Z(t,replace)", apply=True)
row = con.execute("SELECT name FROM t WHERE id = 99").fetchall()
check("update with a hostile string stores it as data", st == "ok" and row == [("x'); DROP TABLE t; --",)], str((st, r, row)))
st, r = run("-nothing_here", apply=True)
check("dropping a table that is not there is refused", st == "refused")
run("@t|Q(id==99)|Z(t,replace)", apply=True)
intact("insert/update attacks")

# --- names are not text: they must be plain words ---
for label, prog in [
    ("statement chaining after a source", "@t;DROP TABLE t"),
    ("UNION in a source name", '@t" UNION SELECT * FROM secrets --'),
    ("a second source via comma", "@t,secrets|C"),
    ("SQL in a field name", '@t|P(name"; DROP TABLE t; --)'),
    ("SQL in a new table name", "I(x; DROP TABLE t,a)"),
    ("path-like table name", "@t|Z(../x)"),
    ("quote in a table name", '@t|Z(a"b)'),
    ("dot-qualified name", "@main.t|C"),
    ("comment markers", "@t|C -- DROP TABLE t"),
    ("semicolon in a saved-line name", "a;b=@t|C"),
]:
    st, r = run(prog, apply=True)
    check(f"refused: {label}", st == "refused", str((st, str(r)[:80])))
intact("name attacks")

# --- expressions: only listed functions run ---
for label, prog in [
    ("import", '@t|A(z=__import__("os").system("echo hi"))'),
    ("open a file", '@t|A(z=open("f"))'),
    ("lambda", "@t|A(z=(lambda: 1)())"),
    ("attribute access", '@t|A(z=name.upper())'),
    ("subscript", "@t|A(z=name[0])"),
    ("comprehension", "@t|A(z=[x for x in name])"),
    ("SQL keyword as a function", '@t|A(z=sqlite_version())'),
    ("SQL subquery smuggled as a string", '@t|A(z=cat(name,"x"))|Y(z="1) UNION SELECT v FROM secrets --",id==1)|P(z)'),
]:
    st, r = run(prog)
    leaked = isinstance(r, list) and any("hunter2" in str(x) for x in r)
    check(f"safe: {label}", st in ("refused", "ok") and not leaked and st != "DB ERROR", str((st, str(r)[:90])))
intact("expression attacks")

st, r = run("@secrets|P(v)")
check("(control) a legitimate read of another table still works", st == "ok" and r == [{"v": "hunter2"}], str((st, r)))
print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAILED'}")
