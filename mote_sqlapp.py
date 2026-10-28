"""Mote app backend for a SQL database (DuckDB or SQLite file). Same interface as mote_app's CSV Store + run_program,
so the MCP server (and any app) can sit on a real database.

  python mote_sqlapp.py import <db file> <folder of CSVs>      load every CSV as a table (.duckdb -> DuckDB, else SQLite)
  python mote_sqlapp.py run <db file> "<program>" [--apply]    one-off run (changes preview unless --apply)
"""
import glob
import hashlib
import json
import os
import re
import sys
import time

import pandas as pd

import mote
import mote_app
import mote_sql


def connect(path):
    if path.lower().endswith(".duckdb"):
        import duckdb
        return duckdb.connect(path)
    import sqlite3
    return sqlite3.connect(path)


class SqlStore:
    def __init__(self, path):
        self.path = path
        self.con = connect(path)
        self.sess = mote_sql.Session(self.con)

    def describe(self):
        out = {}
        for t in self.sess.names():
            cols = self.sess.columns(t)
            out[t] = {"rows": self.sess.count(t), "fields": [f"{c} ({k})" for c, k in cols]}
        return out

    def restore(self, name):
        return self.sess.restore(name)

    def run_program(self, program, apply=False, who="ui"):
        """Same result shape and the same run log as mote_app.run_program."""
        t0 = time.perf_counter()
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "who": who, "apply": bool(apply), "program": program, "db": os.path.basename(self.path)}
        try:
            result, changes = self.sess.run(program, apply=apply)
            result = mote_app.clean(result)
            entry["ok"] = True
            entry["result_sha"] = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()[:12]
            out = {"ok": True, "result": result}
            if changes:
                out["changes"], out["saved"] = changes, bool(apply)
                entry["writes"] = changes
        except mote.MoteError as e:
            entry.update(ok=False, error=str(e))
            out = {"ok": False, "error": str(e)}
        except Exception as e:      # keep the server alive; the log keeps the evidence
            entry.update(ok=False, error=f"internal error {type(e).__name__}: {e}")
            out = {"ok": False, "error": entry["error"]}
        entry["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        out["ms"] = entry["ms"]
        mote_app.log(entry)
        return out


def import_csvs(path, folder):
    store = SqlStore(path)
    made = []
    for p in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        name = os.path.splitext(os.path.basename(p))[0]
        if not re.fullmatch(r"\w+", name):
            continue
        df = pd.read_csv(p)
        if "duckdb" in type(store.con).__module__:
            store.con.register("_imp", df)
            store.con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _imp')
            store.con.unregister("_imp")
        else:
            df.to_sql(name, store.con, index=False, if_exists="replace")
        made.append((name, len(df)))
    if hasattr(store.con, "commit"):
        store.con.commit()
    return made


if __name__ == "__main__":
    a = sys.argv
    if len(a) > 3 and a[1] == "import":
        for n, r in import_csvs(a[2], a[3]):
            print(f"loaded {n}: {r} rows")
    elif len(a) > 3 and a[1] == "run":
        print(json.dumps(SqlStore(a[2]).run_program(a[3], apply="--apply" in a, who="cli"), indent=2, default=str))
    else:
        print(__doc__)
