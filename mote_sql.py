"""Mote on a SQL database (milestone 1: SQLite). Compiles each Mote line to one SQL statement (a chain of CTEs).

Same language, same answers, same safety stops as mote_fast.py (acceptance test: diff_sql.py).
Reads: every step and function. Writes: I + - Q Y Z run inside a transaction: preview = ROLLBACK, apply = COMMIT,
and a backup copy of any table that is overwritten or dropped is kept (_bk_<table>_<time>).
Row order is carried in a hidden column _o (a monotone number, not renumbered unless a sort needs it).
"""
import ast
import datetime
import re
import time

import numpy as np

import mote_expr
import mote_fast
from mote import MoteError, match_tol, parse_cond, split_top

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
NAME = re.compile(r"^\w+$")


class Unsupported(Exception):
    pass


def q(name):
    return '"' + name.replace('"', '""') + '"'


def lit(s):
    return "'" + s.replace("'", "''") + "'"


# ---- SQLite helpers (user-defined functions that match Python/numpy exactly) ---------------------------------
def _fin(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _num_text(s):
    s = (s or "").strip()
    try:
        return int(s)
    except ValueError:
        try:
            return float(s) if np.isfinite(float(s)) else None
        except ValueError:
            return None


def _keep_int(x):
    return int(x) if x is not None and float(x).is_integer() and abs(x) < 1e15 else x


class _Agg:
    def __init__(self):
        self.v, self.p = [], None

    def step(self, x, p=None):
        if x is not None:
            self.v.append(float(x))
        self.p = p


class Median(_Agg):
    def finalize(self):
        return float(np.median(self.v)) if self.v else None


class Std(_Agg):
    def finalize(self):
        return float(np.std(self.v, ddof=1)) if len(self.v) > 1 else None


class Var(_Agg):
    def finalize(self):
        return float(np.var(self.v, ddof=1)) if len(self.v) > 1 else None


class Pct(_Agg):
    def finalize(self):
        return float(np.percentile(self.v, self.p)) if self.v else None


class Corr:
    def __init__(self):
        self.a, self.b = [], []

    def step(self, x, y):
        if x is not None and y is not None:
            self.a.append(float(x))
            self.b.append(float(y))

    def finalize(self):
        if len(self.a) < 2:
            return None
        r = float(np.corrcoef(self.a, self.b)[0, 1])
        return r if np.isfinite(r) else None


def register(con):
    f = con.create_function
    f("a_upper", 1, lambda s: None if s is None else str(s).upper(), deterministic=True)
    f("a_lower", 1, lambda s: None if s is None else str(s).lower(), deterministic=True)
    f("a_trim", 1, lambda s: None if s is None else str(s).strip(), deterministic=True)
    f("a_round", 2, lambda x, n: None if x is None else _keep_int(np.round(x, int(n))) if isinstance(x, float) else x, deterministic=True)
    f("a_sqrt", 1, lambda x: None if x is None else _fin(np.sqrt(x)) if x >= 0 else None, deterministic=True)
    f("a_exp", 1, lambda x: None if x is None else _fin(np.exp(x)), deterministic=True)
    f("a_log", 1, lambda x: None if x is None or x <= 0 else _fin(np.log(x)), deterministic=True)
    f("a_sign", 1, lambda x: None if x is None else float(np.sign(x)), deterministic=True)
    f("a_floor", 1, lambda x: None if x is None else float(np.floor(x)), deterministic=True)
    f("a_ceil", 1, lambda x: None if x is None else float(np.ceil(x)), deterministic=True)
    f("a_pow", 2, lambda a, b: None if a is None or b is None else _fin(np.power(float(a), float(b))) if not (isinstance(a, int) and isinstance(b, int) and b >= 0) else int(a) ** int(b), deterministic=True)
    f("a_mod", 2, lambda a, b: None if a is None or b is None or b == 0 else _keep_int(_fin(np.mod(a, b))) if (isinstance(a, float) or isinstance(b, float)) else int(a) % int(b), deterministic=True)
    f("a_num", 1, _num_text, deterministic=True)
    f("a_weekday", 1, lambda s: WEEKDAYS[datetime.date.fromisoformat(s[:10]).weekday()] if s and len(s) >= 10 else None, deterministic=True)
    con.create_aggregate("a_median", 1, Median)
    con.create_aggregate("a_sd", 1, Std)
    con.create_aggregate("a_var", 1, Var)
    con.create_aggregate("a_pc", 2, Pct)
    con.create_aggregate("a_corr", 2, Corr)


def kind_of(decl, con, table, col):
    d = (decl or "").upper()
    if d:
        return "num" if any(t in d for t in ("INT", "REAL", "FLOA", "DOUB", "NUM")) else "text"
    r = con.execute(f"SELECT typeof({q(col)}) FROM {q(table)} WHERE {q(col)} IS NOT NULL LIMIT 1").fetchone()
    return "num" if r and r[0] in ("integer", "real") else "text"



# ---- database dialects: everything that differs between databases lives here ---------------------------------
def _part(fmt):
    return lambda self, d: f"CAST(strftime('{fmt}', {d}) AS INTEGER)"


class SQLite:
    name = "sqlite"
    hybrid_match = False

    def prepare(self, con):
        con.isolation_level = None          # we control transactions: preview = ROLLBACK, apply = COMMIT
        register(con)

    def text_src(self, c): return f"COALESCE({q(c)},'')"
    def contains(self, col, val): return f"INSTR(a_lower({col}), a_lower({val})) > 0"
    def agg(self, sym, c): return {"S": f"SUM({c})", "V": f"AVG({c})", "X": f"MAX({c})", "N": f"MIN({c})", "M": f"a_median({c})", "SD": f"a_sd({c})", "VR": f"a_var({c})"}[sym]
    def pc(self, c, p): return f"a_pc({c}, {p!r})"
    def corr(self, a, b): return f"a_corr({a}, {b})"
    def rnd(self, x, n): return f"a_round({x}, {n})"
    def div(self, a, b): return f"({a} * 1.0 / {b})"
    def mod(self, a, b): return f"a_mod({a}, {b})"
    def pow(self, a, b): return f"a_pow({a}, {b})"
    def unary(self, f, x): return f"a_{f}({x})"
    def least(self, a, b): return f"min({a}, {b})"
    def greatest(self, a, b): return f"max({a}, {b})"
    def upper(self, x): return f"a_upper({x})"
    def lower(self, x): return f"a_lower({x})"
    def trim(self, x): return f"a_trim({x})"
    def right(self, x, k): return f"substr({x}, -{k})"
    def num(self, x): return f"a_num({x})"
    year = _part("%Y")
    month = _part("%m")
    day = _part("%d")
    def weekday(self, d): return f"a_weekday({d})"
    def addays(self, d, n): return f"strftime('%Y-%m-%d', {d}, printf('%+f days', {n}))"
    def days(self, d1, d2): return f"CAST(ROUND(julianday({d2}) - julianday({d1})) AS INTEGER)"
    def bad_date(self, d): return f"julianday({d}) IS NULL"
    def create_cols(self, fields): return ", ".join(q(f) for f in fields)

    def kind(self, decl, con, table, col):
        d = (decl or "").upper()
        if d:
            return "num" if any(t in d for t in ("INT", "REAL", "FLOA", "DOUB", "NUM")) else "text"
        r = con.execute(f"SELECT typeof({q(col)}) FROM {q(table)} WHERE {q(col)} IS NOT NULL LIMIT 1").fetchone()
        return "num" if r and r[0] in ("integer", "real") else "text"

    def tables(self, con, include_backups=False):
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' UNION SELECT name FROM sqlite_temp_master WHERE type='table'").fetchall()
        return sorted(n for (n,) in rows if not n.startswith("sqlite_") and (include_backups or not n.startswith("_bk_")))

    def drop_temp(self, name): return f"DROP TABLE IF EXISTS temp.{q(name)}"
    def temp_ref(self, name): return f"temp.{q(name)}"
    def adapt_empty(self, con, table, cols, vals): pass


class Duck:
    name = "duckdb"
    hybrid_match = True                     # tolerance matching is computed in Python on the two key columns

    def prepare(self, con): pass

    @staticmethod
    def fin(x): return f"(CASE WHEN isfinite({x}) THEN {x} END)"

    def text_src(self, c): return f"COALESCE(CAST({q(c)} AS VARCHAR),'')"
    def contains(self, col, val): return f"instr(lower({col}), lower({val})) > 0"
    def agg(self, sym, c): return {"S": f"SUM({c})", "V": f"AVG({c})", "X": f"MAX({c})", "N": f"MIN({c})", "M": f"median({c})", "SD": f"stddev_samp({c})", "VR": f"var_samp({c})"}[sym]
    def pc(self, c, p): return f"quantile_cont({c}, {p / 100.0!r})"
    def corr(self, a, b): return f"corr({a}, {b})"
    def rnd(self, x, n): return f"round_even({x}, {n})"
    def div(self, a, b): return self.fin(f"({a} * 1.0 / {b})")
    def mod(self, a, b): return f"((({a}) % ({b}) + ({b})) % ({b}))"
    def pow(self, a, b): return self.fin(f"power({a}, {b})")
    def unary(self, f, x):
        if f == "sqrt": return f"sqrt(CASE WHEN {x} >= 0 THEN {x} END)"
        if f == "log": return f"ln(CASE WHEN {x} > 0 THEN {x} END)"
        if f == "exp": return self.fin(f"exp({x})")
        return {"floor": f"floor({x})", "ceil": f"ceil({x})", "sign": f"sign({x})"}[f]
    def least(self, a, b): return f"least({a}, {b})"
    def greatest(self, a, b): return f"greatest({a}, {b})"
    def upper(self, x): return f"upper({x})"
    def lower(self, x): return f"lower({x})"
    def trim(self, x): return f"regexp_replace({x}, '^[[:space:]]+|[[:space:]]+$', '', 'g')"
    def right(self, x, k): return f"right({x}, {k})"
    def num(self, x): return self.fin(f"try_cast(trim({x}) AS DOUBLE)")
    def _d(self, d): return f"try_strptime({d}, '%Y-%m-%d')"
    def year(self, d): return f"year({self._d(d)})"
    def month(self, d): return f"month({self._d(d)})"
    def day(self, d): return f"day({self._d(d)})"
    def weekday(self, d): return f"strftime({self._d(d)}, '%a')"
    def addays(self, d, n): return f"strftime({self._d(d)} + INTERVAL 1 DAY * ({n}), '%Y-%m-%d')"
    def days(self, d1, d2): return f"datediff('day', {self._d(d1)}, {self._d(d2)})"
    def bad_date(self, d): return f"{self._d(d)} IS NULL"
    def create_cols(self, fields): return ", ".join(f"{q(f)} VARCHAR" for f in fields)

    def kind(self, decl, con, table, col):
        d = (decl or "").upper()
        return "num" if any(t in d for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL", "NUMERIC")) else "text"

    def tables(self, con, include_backups=False):
        rows = con.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        return sorted({n for (n,) in rows if include_backups or not n.startswith("_bk_")})

    def drop_temp(self, name): return f"DROP TABLE IF EXISTS temp.main.{q(name)}"
    def temp_ref(self, name): return f"temp.main.{q(name)}"

    def adapt_empty(self, con, table, cols, vals):
        """A table made by I() has no types yet; the first insert gives its columns their types (like pandas does)."""
        for (c, k), v in zip(cols, vals):
            if k == "text" and not isinstance(v, str):
                con.execute(f"ALTER TABLE {q(table)} ALTER COLUMN {q(c)} SET DATA TYPE {'BIGINT' if isinstance(v, int) else 'DOUBLE'}")


def dialect_for(con):
    return Duck() if "duckdb" in type(con).__module__ else SQLite()


# ---- compiling one Mote line ---------------------------------------------------------------------------------
class Compile:
    def __init__(self, sess, steps):
        self.s, self.con, self.d = sess, sess.con, sess.d
        self.ctes, self.checks, self.n = [], [], 0
        self.z = None
        cur, cols, scalar = None, None, None
        for i, (sym, arg) in enumerate(steps):
            if scalar is not None:
                if sym == "R":
                    scalar = ("round", scalar, int(split_top(arg, ",")[0]))
                    continue
                raise MoteError(f"{sym}: needs a table as input, got a single value")
            if i == 0:
                if not sym.startswith("@"):
                    raise MoteError(f"line must start with a source like @name, got '{sym}'")
                cur, cols = self.source(sym[1:])
                continue
            if sym.startswith("@"):
                raise MoteError(f"{sym}: a source may only start a line")
            if self.z:
                raise Unsupported("steps after Z")
            if "$" in arg:
                raise Unsupported("variables")
            args = split_top(arg, ",") if arg.strip() else []
            cur, cols, scalar = self.step(sym, arg, args, cur, cols)
        self.cur, self.cols, self.scalar = cur, cols, scalar

    # ---- plumbing
    def new(self, sql, materialized=False):
        self.n += 1
        self.ctes.append(f"s{self.n} AS {'MATERIALIZED ' if materialized else ''}({sql})")
        return f"s{self.n}"

    def chain(self, upto):
        return "WITH RECURSIVE " + ", ".join(self.ctes[: int(upto[1:])]) + " "

    def chk(self, sql, msg):
        self.checks.append((f"s{self.n}", sql, msg))

    def source(self, t):
        info = self.s.columns(t)
        sel = ", ".join((f"{self.d.text_src(c)} AS {q(c)}" if k == "text" else q(c)) for c, k in info)
        return self.new(f"SELECT {sel}, rowid AS _o FROM {q(t)}"), info

    @staticmethod
    def field(cols, f, step):
        for c, k in cols:
            if c == f:
                return k
        raise MoteError(f"{step}: unknown field '{f}'. fields: {sorted(c for c, _ in cols)}")

    def complete(self, cur, cols, f, step):
        if self.field(cols, f, step) == "num":
            self.chk(f"SELECT COUNT(*) FROM {cur} WHERE {q(f)} IS NULL",
                     f"{step}: field '{f}' has blank values. Keep only rows that have one first, "
                     f"e.g. F({f}>-1000000000) (a number test drops blank rows)")

    @staticmethod
    def cs(cols, alias=""):
        a = alias + "." if alias else ""
        return ", ".join(a + q(c) for c, _ in cols)

    # ---- steps
    def step(self, sym, arg, args, cur, cols):
        cs = self.cs
        if sym == "F":
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} WHERE {self.cond(parse_cond(arg), cols)}"), cols, None
        if sym == "Q":
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} WHERE NOT COALESCE(({self.cond(parse_cond(arg), cols)}), 0)"), cols, None
        if sym == "P":
            if any("=" in a for a in args):
                raise Unsupported("P with renaming")
            nc = [(f, self.field(cols, f, "P")) for f in args]
            return self.new(f"SELECT {cs(nc)}, _o FROM {cur}"), nc, None
        if sym in ("O", "D"):
            if len(args) != 1:
                raise MoteError(f"{sym}: needs exactly one field")
            self.field(cols, args[0], sym)
            order = f"({q(args[0])} IS NULL), {q(args[0])}{' DESC' if sym == 'D' else ''}, _o"
            return self.new(f"SELECT {cs(cols)}, ROW_NUMBER() OVER (ORDER BY {order}) AS _o FROM {cur}"), cols, None
        if sym == "T":
            if len(args) != 1 or not args[0].isdigit():
                raise MoteError("T: needs one whole number")
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} ORDER BY _o LIMIT {int(args[0])}"), cols, None
        if sym == "U":
            for f in args:
                self.field(cols, f, "U")
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} WHERE _o IN (SELECT MIN(_o) FROM {cur} GROUP BY {', '.join(q(f) for f in args)})"), cols, None
        if sym == "G":
            return self.group(args, cur, cols)
        if sym in ("S", "V", "X", "N", "C", "M", "SD", "VR", "PC", "CO"):
            return self.scalar_step(sym, args, cur, cols)
        if sym == "J":
            return self.join(args, cur, cols)
        if sym == "W":
            if len(args) != 2 or not args[0].startswith("@"):
                raise MoteError("W: needs (@source,keyfield)")
            rc, rcols = self.source(args[0][1:])
            self.field(cols, args[1], "W")
            self.field(rcols, args[1], "W")
            k = q(args[1])
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} WHERE NOT EXISTS (SELECT 1 FROM {rc} WHERE {rc}.{k} = {cur}.{k})"), cols, None
        if sym == "K":
            if len(args) != 1:
                raise MoteError("K: needs one field")
            self.field(cols, args[0], "K")
            if any(c == "cum" for c, _ in cols):
                raise MoteError("K: field 'cum' already exists")
            self.complete(cur, cols, args[0], "K")
            return self.new(f"SELECT {cs(cols)}, SUM({q(args[0])}) OVER (ORDER BY _o) AS cum, _o FROM {cur}"), cols + [("cum", "num")], None
        if sym == "B":
            if len(args) != 2 or not args[1].isdigit():
                raise MoteError("B: needs (keyfield,n) e.g. B(team,2)")
            self.field(cols, args[0], "B")
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} WHERE _o IN (SELECT _o FROM (SELECT _o, ROW_NUMBER() OVER (PARTITION BY {q(args[0])} ORDER BY _o) AS r FROM {cur}) WHERE r <= {int(args[1])})"), cols, None
        if sym == "H":
            if len(args) != 2 or not args[1].isdigit():
                raise MoteError("H: needs (field,decimals) e.g. H(v,1)")
            self.field(cols, args[0], "H")
            if any(c == "pct" for c, _ in cols):
                raise MoteError("H: field 'pct' already exists")
            self.complete(cur, cols, args[0], "H")
            self.chk(f"SELECT CASE WHEN COALESCE(SUM({q(args[0])}),0) = 0 THEN 1 ELSE 0 END FROM {cur}", "H: column total is zero")
            return self.new(f"SELECT {cs(cols)}, {self.d.rnd(f'{q(args[0])} * 100.0 / (SELECT SUM({q(args[0])}) FROM {cur})', int(args[1]))} AS pct, _o FROM {cur}"), cols + [("pct", "num")], None
        if sym == "A":
            return self.derive(arg, cur, cols)
        if sym == "Y":
            return self.update(args, cur, cols)
        if sym == "L":
            return self.tol_match(args, cur, cols)
        if sym == "E":
            if len(args) != 1 or not args[0].startswith("@"):
                raise MoteError("E: needs (@source)")
            rc, rcols = self.source(args[0][1:])
            if {c for c, _ in cols} != {c for c, _ in rcols}:
                raise MoteError(f"E: both tables need the same fields. left: {sorted(c for c, _ in cols)} right: {sorted(c for c, _ in rcols)}")
            return self.new(f"SELECT {cs(cols)}, _o FROM {cur} UNION ALL SELECT {cs(cols)}, _o + (SELECT COALESCE(MAX(_o),0) FROM {cur}) FROM {rc}"), cols, None
        if sym == "Z":
            if not 1 <= len(args) <= 2 or not NAME.match(args[0]) or (len(args) == 2 and args[1] not in ("replace", "add")):
                raise MoteError("Z: needs (name), (name,replace) or (name,add)")
            self.z = (args[0], args[1] if len(args) == 2 else "create")
            return cur, cols, None
        raise Unsupported(sym)

    def cond(self, groups, cols):
        ors = []
        for terms in groups:
            ands = []
            for f, op, rhs in terms:
                kind = self.field(cols, f, "F")
                rhs = rhs.strip()
                if len(rhs) >= 2 and rhs[0] in "\"'" and rhs[-1] == rhs[0]:
                    val, rk = lit(rhs[1:-1]), "text"
                else:
                    try:
                        float(rhs)
                        val, rk = rhs, "num"
                    except ValueError:
                        rk = self.field(cols, rhs, "F")
                        val = q(rhs)
                if op in ("~", "!~"):
                    if kind != "text":
                        raise Unsupported("~ on a number field")
                    hit = self.d.contains(q(f), val)
                    ands.append(hit if op == "~" else f"NOT ({hit})")
                else:
                    if kind != rk:
                        raise Unsupported("mixed-type compare")
                    ands.append(f"{q(f)} {op} {val}")
            ors.append("(" + " AND ".join(ands) + ")")
        return " OR ".join(ors)

    def group(self, args, cur, cols):
        if len(args) > 2 or (len(args) == 2 and "=" in args[1]):
            raise Unsupported("G with several or named totals")
        if len(args) != 2:
            raise MoteError("G: needs (keyfield,AGG:field) e.g. G(cat,S:amt)")
        key, a = args[0], args[1].split(":")
        fn = {"S": "SUM", "V": "AVG", "X": "MAX", "N": "MIN", "M": "a_median", "SD": "a_sd", "VR": "a_var"}
        if (a[0] not in fn and a[0] != "C") or (a[0] != "C" and len(a) != 2):
            raise MoteError("G: second arg like S:amt (S sum, C count, V avg, X max, N min, M median, SD std dev, VR variance)")
        kk = self.field(cols, key, "G")
        if len(a) == 2:
            self.field(cols, a[1], "G")
            self.complete(cur, cols, a[1], "G")
        agg = "COUNT(*)" if a[0] == "C" else self.d.agg(a[0], q(a[1]))
        if a[0] in ("SD", "VR"):
            self.chk(f"SELECT COUNT(*) FROM (SELECT 1 FROM {cur} GROUP BY {q(key)} HAVING COUNT(*) < 2)", f"G: {a[0]} needs at least 2 rows in every group")
        where = f" WHERE {q(key)} IS NOT NULL" if kk == "num" else ""
        return self.new(f"SELECT {q(key)}, {agg} AS v, MIN(_o) AS _o FROM {cur}{where} GROUP BY {q(key)}"), [(key, kk), ("v", "num")], None

    def scalar_step(self, sym, args, cur, cols):
        if sym == "C":
            return cur, cols, ("sql", f"SELECT COUNT(*) FROM {cur}")
        want = 2 if sym in ("PC", "CO") else 1
        if len(args) != want:
            raise MoteError(f"{sym}: needs {'(field,percent)' if sym == 'PC' else '(field,field)' if sym == 'CO' else 'one field'}")
        fl = [args[0]] if sym == "PC" else args
        for f in fl:
            self.field(cols, f, sym)
        self.chk(f"SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM {cur}", f"{sym}: no rows to {sym}")
        for f in fl:
            self.complete(cur, cols, f, sym)
        if sym in ("SD", "VR", "CO"):
            self.chk(f"SELECT CASE WHEN COUNT(*) < 2 THEN 1 ELSE 0 END FROM {cur}", f"{sym}: needs at least 2 rows")
        c = q(args[0])
        if sym == "PC":
            try:
                p = float(args[1])
            except ValueError:
                p = -1
            if not 0 <= p <= 100:
                raise MoteError("PC: percent must be a number from 0 to 100, e.g. PC(amt,90)")
            return cur, cols, ("sql", f"SELECT {self.d.pc(c, p)} FROM {cur}")
        if sym == "CO":
            return cur, cols, ("sql", f"SELECT {self.d.corr(c, q(args[1]))} FROM {cur}")
        return cur, cols, ("sql", f"SELECT {self.d.agg(sym, c)} FROM {cur}")

    def join(self, args, cur, cols):
        if len(args) != 2 or not args[0].startswith("@"):
            raise MoteError("J: needs (@source,keyfield)")
        rc, rcols = self.source(args[0][1:])
        key = args[1]
        self.field(cols, key, "J")
        self.field(rcols, key, "J")
        extra = [(c, k) for c, k in rcols if c != key]
        clash = {c for c, _ in extra} & {c for c, _ in cols}
        k = q(key)
        rd = self.new(f"SELECT * FROM {rc} WHERE _o IN (SELECT MAX(_o) FROM {rc} GROUP BY {k})", materialized=True)
        right = rd
        if clash:
            self.chk(f"SELECT COUNT(*) FROM {cur} l WHERE EXISTS (SELECT 1 FROM {right} r WHERE r.{k} = l.{k})",
                     f"J: fields {sorted(clash)} exist on both sides. Trim the right source first, e.g. x=@y|P({key},fieldyouneed) then J(@x,{key})")
        sel = self.cs(cols, "l") + (", " + self.cs(extra, "r") if extra else "")
        return self.new(f"SELECT {sel}, l._o AS _o FROM {cur} l JOIN {right} r ON r.{k} = l.{k}"), cols + extra, None

    def flush_checks(self):
        self.run_checks()
        self.checks = []

    def tol_match_hybrid(self, args, cur, cols):
        if len(args) != 3 or not args[0].startswith("@"):
            raise MoteError("L: needs (@source,numericfield,tolerance) e.g. L(@bank,amt,0.01)")
        rc, rcols = self.source(args[0][1:])
        key = args[1]
        try:
            tol = float(args[2])
        except ValueError:
            raise MoteError("L: tolerance must be a number")
        self.field(cols, key, "L")
        self.field(rcols, key, "L")
        self.complete(cur, cols, key, "L")
        self.complete(rc, rcols, key, "L")
        self.flush_checks()                                         # blank keys must stop the run before matching
        k = q(key)
        lk = self.con.execute(self.chain(cur) + f"SELECT {k}, _o FROM {cur} ORDER BY _o").fetchall()
        rk = self.con.execute(self.chain(rc) + f"SELECT {k}, _o FROM {rc} ORDER BY _o").fetchall()
        pairs = match_tol([r[0] for r in lk], [r[0] for r in rk], tol)
        extra = [(c, kk) for c, kk in rcols if c != key]
        clash = {c for c, _ in extra} & {c for c, _ in cols}
        if clash and pairs:
            raise MoteError(f"L: fields {sorted(clash)} exist on both sides. Trim the right source first, e.g. x=@y|P({key},fieldyouneed) then L(@x,{key},{args[2]})")
        tmp = f"_mote_pairs_{len(self.s.state['temps'])}"
        self.con.execute(f"CREATE TEMP TABLE {q(tmp)} (lo BIGINT, ro BIGINT)")
        self.s.state["temps"].append(tmp)
        if pairs:
            self.con.executemany(f"INSERT INTO {q(tmp)} VALUES (?, ?)", [(lk[i][1], rk[j][1]) for i, j in pairs])
        sel = self.cs(cols, "l") + (", " + self.cs(extra, "r") if extra else "")
        out = self.new(f"SELECT {sel}, l._o AS _o FROM {q(tmp)} p JOIN {cur} l ON l._o = p.lo JOIN {rc} r ON r._o = p.ro")
        return out, cols + extra, None

    def tol_match(self, args, cur, cols):
        if self.d.hybrid_match:
            return self.tol_match_hybrid(args, cur, cols)
        if len(args) != 3 or not args[0].startswith("@"):
            raise MoteError("L: needs (@source,numericfield,tolerance) e.g. L(@bank,amt,0.01)")
        rc, rcols = self.source(args[0][1:])
        key = args[1]
        try:
            tol = float(args[2])
        except ValueError:
            raise MoteError("L: tolerance must be a number")
        self.field(cols, key, "L")
        self.field(rcols, key, "L")
        self.complete(cur, cols, key, "L")
        self.complete(rc, rcols, key, "L")
        k = q(key)
        lc = self.new(f"SELECT {k} AS kv, _o, ROW_NUMBER() OVER (ORDER BY _o) AS rn FROM {cur}")
        m = f"s{self.n + 1}"
        cand = (f"(SELECT r._o FROM {rc} r WHERE instr({m}.used, ',' || r._o || ',') = 0 AND ABS(r.{k} - "
                f"(SELECT l.kv FROM {lc} l WHERE l.rn = {m}.n + 1)) <= {tol!r} ORDER BY r._o LIMIT 1)")
        self.new(f"SELECT 0 AS n, ',' AS used, NULL AS ri UNION ALL SELECT {m}.n + 1, {m}.used || COALESCE({cand} || ',', ''), {cand} "
                 f"FROM {m} WHERE {m}.n < (SELECT COUNT(*) FROM {lc})")
        extra = [(c, kk) for c, kk in rcols if c != key]
        clash = {c for c, _ in extra} & {c for c, _ in cols}
        if clash:
            self.chk(f"SELECT COUNT(*) FROM {m} WHERE n >= 1 AND ri IS NOT NULL",
                     f"L: fields {sorted(clash)} exist on both sides. Trim the right source first, e.g. x=@y|P({key},fieldyouneed) then L(@x,{key},{args[2]})")
        sel = self.cs(cols, "l") + (", " + self.cs(extra, "r") if extra else "")
        out = self.new(f"SELECT {sel}, l._o AS _o FROM {m} JOIN {lc} lr ON lr.rn = {m}.n JOIN {cur} l ON l._o = lr._o JOIN {rc} r ON r._o = {m}.ri WHERE {m}.n >= 1 AND {m}.ri IS NOT NULL")
        return out, cols + extra, None

    # ---- expressions (A and Y)
    def expr(self, text, cur, cols):
        """Returns (sql, kind). Mirrors mote_expr: same functions, same type rules, same error messages."""
        try:
            tree = ast.parse(text.strip(), mode="eval").body
        except SyntaxError:
            raise MoteError(f"A: bad expression '{text}'")
        used, dates = [], []

        def need_num(x, what):
            if x[1] != "num":
                raise MoteError(f"A: {what} needs numbers, got text" + ("" if isinstance(x[2], str) else ". Use num(field) to convert text to a number"))
            return x[0]

        def walk(n):
            if isinstance(n, ast.Constant):
                if isinstance(n.value, bool) or not isinstance(n.value, (int, float, str)):
                    raise MoteError("A: only numbers and quoted text are allowed")
                if isinstance(n.value, str):
                    return (lit(n.value), "text", "const")
                return (repr(n.value), "num", "const")
            if isinstance(n, ast.Name):
                k = self.field(cols, n.id, "A")
                used.append(n.id)
                return (q(n.id), k, None)
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
                v = need_num(walk(n.operand), "-")
                return (f"(-{v})" if isinstance(n.op, ast.USub) else v, "num", None)
            if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)):
                a = need_num(walk(n.left), "+ - * / % **")
                b = need_num(walk(n.right), "+ - * / % **")
                if isinstance(n.op, ast.Div):
                    return (self.d.div(a, b), "num", None)
                if isinstance(n.op, ast.Mod):
                    return (self.d.mod(a, b), "num", None)
                if isinstance(n.op, ast.Pow):
                    return (self.d.pow(a, b), "num", None)
                return (f"({a} {'+' if isinstance(n.op, ast.Add) else '-' if isinstance(n.op, ast.Sub) else '*'} {b})", "num", None)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "fill" and not n.keywords:
                if len(n.args) != 2 or not isinstance(n.args[0], ast.Name) or not isinstance(n.args[1], ast.Constant):
                    raise MoteError("A: fill(field,value) takes a field name and a plain number or quoted text")
                name, v = n.args[0].id, n.args[1].value
                k = self.field(cols, name, "A")
                if (k == "num") != (not isinstance(v, str)):
                    raise MoteError(f"A: fill({name},...) needs {'a number' if k == 'num' else 'quoted text'}")
                if k == "num":
                    return (f"COALESCE({q(name)}, {v!r})", "num", None)
                return (f"CASE WHEN {q(name)} = '' THEN {lit(v)} ELSE {q(name)} END", "text", None)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
                return call(n.func.id, n)
            if isinstance(n, (ast.Compare, ast.BoolOp, ast.IfExp)) or (isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not)):
                raise Unsupported("conditions in expressions")
            raise MoteError("A: bad expression (use numbers, fields, + - * / % **, and the listed functions)")

        def call(f, n):
            if f not in mote_expr.FUNCS:
                raise MoteError(f"A: unknown function '{f}'. functions: {sorted(mote_expr.FUNCS)}")
            lo, hi, _ = mote_expr.FUNCS[f]
            if not lo <= len(n.args) <= hi:
                raise MoteError(f"A: {f}() takes {lo}{'' if lo == hi else f'-{hi}'} argument(s)")
            a = [walk(x) for x in n.args]
            for i, x in enumerate(a):
                if f in ("left", "right", "round") and i == 1 and not (x[2] == "const" and x[1] == "num"):
                    raise MoteError(f"A: {f}() needs a plain whole number as its second argument")
            txt = lambda x: f"CAST({x[0]} AS TEXT)"
            num = lambda x, w: need_num(x, w + "()")
            if f == "abs": return (f"abs({num(a[0], f)})", "num", None)
            if f == "round": return (self.d.rnd(num(a[0], f), int(float(a[1][0])) if len(a) == 2 else 0), "num", None)
            if f in ("floor", "ceil", "sqrt", "exp", "log", "sign"): return (self.d.unary(f, num(a[0], f)), "num", None)
            if f == "pow": return (self.d.pow(num(a[0], f), num(a[1], f)), "num", None)
            if f == "mod": return (self.d.mod(num(a[0], f), num(a[1], f)), "num", None)
            if f == "least": return (self.d.least(num(a[0], f), num(a[1], f)), "num", None)
            if f == "greatest": return (self.d.greatest(num(a[0], f), num(a[1], f)), "num", None)
            if f in ("upper", "lower", "trim"): return (getattr(self.d, f)(txt(a[0])), "text", None)
            if f == "len": return (f"length({txt(a[0])})", "num", None)
            if f == "left": return (f"substr({txt(a[0])}, 1, {int(float(a[1][0]))})", "text", None)
            if f == "right":
                k = int(float(a[1][0]))
                return (self.d.right(txt(a[0]), k) if k > 0 else "''", "text", None)
            if f == "replace": return (f"replace({txt(a[0])}, {lit(str(n.args[1].value)) if isinstance(n.args[1], ast.Constant) else a[1][0]}, {lit(str(n.args[2].value)) if isinstance(n.args[2], ast.Constant) else a[2][0]})", "text", None)
            if f == "cat": return ("(" + " || ".join(txt(x) for x in a) + ")", "text", None)
            if f == "num":
                e = self.d.num(txt(a[0]))
                self.numchecks.append((e, "A: num() found text that is not a number"))
                return (e, "num", None)
            if f == "ym": return (f"substr({txt(a[0])}, 1, 7)", "text", None)
            d10 = lambda x: f"substr({txt(x)}, 1, 10)"
            if f in ("year", "month", "day", "weekday", "addays", "days"):
                for x in (a[:2] if f == "days" else a[:1]):
                    dates.append((d10(x), f))
            if f in ("year", "month", "day"): return (getattr(self.d, f)(d10(a[0])), "num", None)
            if f == "weekday": return (self.d.weekday(d10(a[0])), "text", None)
            if f == "addays": return (self.d.addays(d10(a[0]), need_num((a[1][0], a[1][1], None), 'addays()')), "text", None)
            if f == "days": return (self.d.days(d10(a[0]), d10(a[1])), "num", None)
            raise Unsupported(f)

        self.numchecks = []
        sql, kind, _ = walk(tree)
        return sql, kind, used, dates, list(self.numchecks)

    def expr_checks(self, cur, cols, used, dates, numchecks, fill_fields=()):
        seen = set()
        for e, f in dates:
            if (e, f) in seen:
                continue
            seen.add((e, f))
            self.chk(f"SELECT COUNT(*) FROM {cur} WHERE {e} <> '' AND {self.d.bad_date(e)}", f"A: {f}() needs dates written YYYY-MM-DD")
            self.chk(f"SELECT COUNT(*) FROM {cur} WHERE {e} = ''", f"A: {f}() needs dates written YYYY-MM-DD (some are blank; drop them first, e.g. F(date!=\"\"))")
        for f in dict.fromkeys(used):
            if f not in fill_fields:
                self.complete(cur, cols, f, "A")

    def derive(self, arg, cur, cols):
        if len(split_top(arg, ",")) > 1:
            raise Unsupported("A with several assignments")
        if "=" not in arg or arg.split("=")[0].strip() == "":
            raise MoteError("A: needs new=expression")
        new, text = [p.strip() for p in arg.split("=", 1)]
        if any(c == new for c, _ in cols):
            raise MoteError(f"A: field '{new}' already exists")
        sql, kind, used, dates, numchecks = self.expr(text, cur, cols)
        for e, msg in numchecks:
            self.chk(f"SELECT COUNT(*) FROM {cur} WHERE {e} IS NULL", msg)
        self.expr_checks(cur, cols, [u for u in used if u not in self.fill_names(text)], dates, [])
        name = self.new(f"SELECT {self.cs(cols)}, {sql} AS {q(new)}, _o FROM {cur}")
        if kind == "num":
            self.chk(f"SELECT COUNT(*) FROM {name} WHERE {q(new)} IS NULL", "A: the calculation gave an invalid number (divide by zero, or sqrt/log of a bad value)")
        return name, cols + [(new, kind)], None

    @staticmethod
    def fill_names(text):
        return set(re.findall(r"fill\(\s*(\w+)\s*,", text))

    def update(self, args, cur, cols):
        if not 1 <= len(args) <= 2:
            raise MoteError("Y: needs (field=expression) or (field=expression,condition)")
        m = re.match(r"^\s*(\w+)\s*=(?!=)\s*(.+)$", args[0])
        if not m:
            raise MoteError("Y: needs (field=expression) or (field=expression,condition)")
        field, text = m.group(1), m.group(2)
        old = None
        for c, k in cols:
            if c == field:
                old = k
        if old is None:
            raise MoteError(f"Y: unknown field '{field}'. fields: {sorted(c for c, _ in cols)} (add a new field with A first)")
        sql, kind, used, dates, numchecks = self.expr(text, cur, cols)
        for e, msg in numchecks:
            self.chk(f"SELECT COUNT(*) FROM {cur} WHERE {e} IS NULL", msg)
        self.expr_checks(cur, cols, [u for u in used if u not in self.fill_names(text)], dates, [])
        if (old == "num") != (kind == "num"):
            raise MoteError(f"Y: field '{field}' holds {'numbers' if old == 'num' else 'text'}; give {'a number' if old == 'num' else 'text in quotes'}")
        if kind == "num":
            self.chk(f"SELECT COUNT(*) FROM (SELECT {sql} AS v FROM {cur}) WHERE v IS NULL", "A: the calculation gave an invalid number (divide by zero, or sqrt/log of a bad value)")
        mask = f"COALESCE(({self.cond(parse_cond(args[1]), cols)}), 0)" if len(args) == 2 else "1"
        sel = ", ".join((f"CASE WHEN {mask} THEN {sql} ELSE {q(c)} END AS {q(c)}" if c == field else q(c)) for c, _ in cols)
        return self.new(f"SELECT {sel}, _o FROM {cur}"), cols, None

    # ---- run
    def run_checks(self):
        by_cte = {}
        for cte, sql, msg in self.checks:
            by_cte.setdefault(cte, []).append((sql, msg))
        for cte in sorted(by_cte, key=lambda c: int(c[1:])):
            items = by_cte[cte]
            row = self.con.execute(self.chain(cte) + "SELECT " + ", ".join(f"({sql})" for sql, _ in items)).fetchone()
            for v, (_, msg) in zip(row, items):          # first failing check in program order wins
                if v and v > 0:
                    raise MoteError(msg)

    def rows(self):
        cols = [c for c, _ in self.cols]
        data = self.con.execute(self.chain(self.cur) + f"SELECT {', '.join(q(c) for c in cols)} FROM {self.cur} ORDER BY _o").fetchall()
        return [dict(zip(cols, r)) for r in data]

    def value(self):
        val, rounds = self.scalar, []
        while val[0] == "round":
            rounds.append(val[2])
            val = val[1]
        out = self.con.execute(self.chain(self.cur) + val[1]).fetchone()[0]
        for n in rounds:
            if out is None:
                raise MoteError("R: needs a single value and one whole number")
            out = round(out, n)
        return out

    def select_all(self):
        return f"{self.chain(self.cur)}SELECT {self.cs(self.cols)} FROM {self.cur} ORDER BY _o"


# ---- a session on one database -------------------------------------------------------------------------------
class Session:
    def __init__(self, con):
        self.con = con
        self.d = dialect_for(con)
        self.d.prepare(con)
        self.state = {"temps": [], "backed": {}, "real": set(), "saved": set(), "apply": False}

    def names(self):
        return self.d.tables(self.con)

    def columns(self, t):
        info = self.con.execute(f"PRAGMA table_info({q(t)})").fetchall()
        if not info:
            raise MoteError(f"@{t}: no such source. have: {self.names()}")
        return [(r[1], self.d.kind(r[2], self.con, t, r[1])) for r in info]

    def count(self, t):
        return self.con.execute(f"SELECT COUNT(*) FROM {q(t)}").fetchone()[0]

    def backup(self, name, state):
        """Keep a copy of a table before the first change in this run (only when really saving)."""
        if state["apply"] and name not in state["backed"] and name in state["real"]:
            b = f"_bk_{name}_{time.strftime('%Y%m%d%H%M%S')}{int(time.time() * 1e6) % 1000000:06d}"
            self.con.execute(f"CREATE TABLE {q(b)} AS SELECT * FROM {q(name)} ORDER BY rowid")
            state["backed"][name] = b
        return state["backed"].get(name)

    def run(self, program, apply=False):
        """Returns (result, changes). Nothing persists unless apply=True. Any error rolls everything back."""
        plan = mote_fast.parse(program)
        state = {"apply": apply, "backed": {}, "real": set(self.names()), "saved": set(), "temps": []}
        self.state = state
        changes = []
        self.con.execute("BEGIN")
        try:
            result = None
            for item in plan:
                result = self.statement(item[1], state, changes) if item[0] == "stmt" else self.pipeline(item[1], item[2], state, changes)
            final = self.collapse(changes, state)          # row counts are read BEFORE a preview is rolled back
            self.con.execute("COMMIT" if apply else "ROLLBACK")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        finally:
            for t in state["temps"]:
                try:
                    self.con.execute(self.d.drop_temp(t))
                except Exception:
                    pass
        return result, final

    def known(self, state):
        return sorted(set(self.names()) | state["saved"])

    def collapse(self, changes, state):
        final = {}
        for c in changes:
            first = final.get(c["table"])
            final[c["table"]] = {**c, "rows_before": first["rows_before"] if first else c["rows_before"], "first_op": first["first_op"] if first else c["op"]}
        out = [c for c in final.values() if not (c["op"] == "drop" and c["first_op"] == "create")]
        for c in out:
            c["rows_after"] = 0 if c["op"] == "drop" else self.count(c["table"])
            c["backup"] = state["backed"].get(c["table"])
            c.pop("first_op", None)
        return out

    def pipeline(self, save_name, steps, state, changes):
        comp = Compile(self, steps)
        comp.run_checks()
        if comp.z:
            return self.save(comp, state, changes)
        if comp.scalar is not None:
            return comp.value()
        if save_name:
            self.con.execute(self.d.drop_temp(save_name))
            self.con.execute(f"CREATE TEMP TABLE {q(save_name)} AS {comp.select_all()}")
            state["temps"].append(save_name)
            state["saved"].add(save_name)
            return [dict(zip([c for c, _ in comp.cols], r)) for r in self.con.execute(f"SELECT * FROM {self.d.temp_ref(save_name)} ORDER BY rowid").fetchall()]
        return comp.rows()

    def save(self, comp, state, changes):
        name, mode = comp.z
        have = name in self.known(state)
        real = name in self.names() and name not in state["saved"]
        if mode == "create" and have:
            raise MoteError(f"Z: table '{name}' already exists. Use Z({name},replace) to overwrite (a backup is kept) or Z({name},add) to append")
        if have and not real:
            raise Unsupported("Z over a saved line")
        sel = comp.select_all()
        if mode == "add":
            if not have:
                raise MoteError(f"Z: table '{name}' does not exist yet. Use Z({name}) to create it")
            base = self.columns(name)
            if {c for c, _ in base} != {c for c, _ in comp.cols}:
                raise MoteError(f"Z: both tables need the same fields. {name}: {sorted(c for c, _ in base)} new rows: {sorted(c for c, _ in comp.cols)}")
            before = self.count(name)
            self.backup(name, state)
            order = ", ".join(q(c) for c, _ in base)
            self.con.execute(f"INSERT INTO {q(name)} ({order}) SELECT {order} FROM ({sel})")
            op = "add"
        else:
            before = self.count(name) if have else 0
            if have:
                self.backup(name, state)
            tmp = name + "__new"
            self.con.execute(f"DROP TABLE IF EXISTS {q(tmp)}")
            self.con.execute(f"CREATE TABLE {q(tmp)} AS {sel}")
            if have:
                self.con.execute(f"DROP TABLE {q(name)}")
            self.con.execute(f"ALTER TABLE {q(tmp)} RENAME TO {q(name)}")
            op = "replace" if have else "create"
        changes.append({"table": name, "op": op, "rows_before": before})
        return [dict(zip([c for c, _ in self.columns(name)], r)) for r in self.con.execute(f"SELECT * FROM {q(name)} ORDER BY rowid").fetchall()]

    def statement(self, text, state, changes):
        m = re.match(r"^I\((.*)\)$", text)
        if m:
            parts = split_top(m.group(1), ",")
            if len(parts) < 2 or not all(NAME.match(p) for p in parts) or len(set(parts[1:])) != len(parts) - 1:
                raise MoteError("I: needs (tablename,field1,field2,...) with unique plain names")
            name, fields = parts[0], parts[1:]
            if name in self.known(state):
                raise MoteError(f"I: table '{name}' already exists")
            self.con.execute(f"CREATE TABLE {q(name)} ({self.d.create_cols(fields)})")
            changes.append({"table": name, "op": "create", "rows_before": 0})
            return f"created table {name} with fields {fields}"
        m = re.match(r"^\+(\w+)\((.*)\)$", text)
        if m:
            name, vals = m.group(1), [mote_fast._lit(v) for v in split_top(m.group(2), ",")]
            if name not in self.known(state):
                raise MoteError(f"+{name}: no such table. have: {self.known(state)}")
            cols = self.columns(name)
            if len(vals) != len(cols):
                raise MoteError(f"+{name}: needs {len(cols)} values in this order: {[c for c, _ in cols]}")
            before = self.count(name)
            for (c, k), v in zip(cols, vals):
                if before and (k == "num") != (not isinstance(v, str)):
                    raise MoteError(f"+{name}: field '{c}' holds {'numbers' if k == 'num' else 'text'}")
            self.backup(name, state)
            if before == 0:
                self.d.adapt_empty(self.con, name, cols, vals)
            self.con.execute(f"INSERT INTO {q(name)} VALUES ({', '.join('?' for _ in vals)})", vals)
            changes.append({"table": name, "op": "insert", "rows_before": before})
            return f"inserted 1 row into {name} (now {before + 1} rows)"
        name = text[1:]
        if name not in self.known(state):
            raise MoteError(f"-{name}: no such table. have: {self.known(state)}")
        before = self.count(name)
        self.backup(name, state)
        self.con.execute(f"DROP TABLE {q(name)}")
        changes.append({"table": name, "op": "drop", "rows_before": before})
        return f"dropped table {name}"

    def restore(self, name):
        found = sorted(n for n in self.d.tables(self.con, include_backups=True) if n.startswith(f"_bk_{name}_"))
        if not found:
            raise MoteError(f"no backup found for '{name}'")
        b = found[-1]
        self.con.execute("BEGIN")
        if name in self.names():
            cur = f"_bk_{name}_{time.strftime('%Y%m%d%H%M%S')}{int(time.time() * 1e6) % 1000000:06d}"
            self.con.execute(f"CREATE TABLE {q(cur)} AS SELECT * FROM {q(name)} ORDER BY rowid")
            self.con.execute(f"DROP TABLE {q(name)}")
        self.con.execute(f"CREATE TABLE {q(name)} AS SELECT * FROM {q(b)} ORDER BY rowid")
        self.con.execute("COMMIT")
        return b


def run(program, con):
    """Read-only convenience used by the comparison tests: returns just the result (changes are previewed and rolled back)."""
    return Session(con).run(program, apply=False)[0]
