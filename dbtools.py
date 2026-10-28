"""Pick the database for the comparison tests: set MOTE_DB=duckdb (default sqlite)."""
import os

ENGINE = os.environ.get("MOTE_DB", "sqlite")


def connect(path=":memory:"):
    if ENGINE == "duckdb":
        import duckdb
        return duckdb.connect(path)
    import sqlite3
    return sqlite3.connect(path)


def load(con, name, df):
    """Put a pandas table into the database. Columns of mixed types are dropped (same as the SQLite tests did)."""
    mixed = [c for c in df.columns if df[c].dtype == object and df[c].map(type).nunique() > 1]
    df = df.drop(columns=mixed)
    if ENGINE == "duckdb":
        con.register("_df_tmp", df)
        con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM _df_tmp')
        con.unregister("_df_tmp")
    else:
        df.to_sql(name, con, index=False)
