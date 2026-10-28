"""Mote fast engine: same language as mote.py, but a program is read ONCE into a plan and run on columns (pandas).
Same results and same error messages as mote.py (checked by diff_test.py). Needs pandas.
"""
import re
import threading
import numpy as np
import pandas as pd
import mote_expr
import mote_flow
from mote import (MoteError, split_top, parse_cond, AGG, match_tol)

STEP = re.compile(r"^(@\w+|[A-Z]{1,2})\s*(?:\((.*)\))?$")
NUM = lambda tok: float(tok) if "." in tok else int(tok)
NAME = re.compile(r"^\w+$")
G_AGG = {"S": "sum", "V": "mean", "X": "max", "N": "min", "M": "median", "SD": "std", "VR": "var"}
SCALARS = set(AGG) | {"SD", "VR", "PC", "CO"}
_tl = threading.local()


def _fx():
    if not hasattr(_tl, "fx") or _tl.fx is None:
        _tl.fx = []
    return _tl.fx


FN_LINE = re.compile(r"^FN\s+([a-z_]\w*)\s*\(([^)]*)\)\s*=\s*(.+)$")
FN_CALL = re.compile(r"^([a-z_]\w*)\s*(?:\((.*)\))?$")
MAX_FN_DEPTH = 20


def _outside_quotes(text, fn):
    """Apply fn to the parts of text that are not inside quotes."""
    out, seg, q = [], "", None
    for ch in text:
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            out.append(fn(seg))
            seg, q = "", ch
            out.append(ch)
        else:
            seg += ch
    out.append(fn(seg))
    return "".join(out)


def _replace_params(text, sub_map):
    return _outside_quotes(text, lambda seg: re.sub(r"\b[A-Za-z_]\w*\b", lambda m: sub_map.get(m.group(0), m.group(0)), seg))


def _expand(parts, fns, stack=()):
    out = []
    for p in parts:
        p = p.strip()
        m = FN_CALL.match(p)
        if not m:
            out.append(p)
            continue
        name, argtext = m.group(1), m.group(2) or ""
        if name not in fns:
            raise MoteError(f"unknown function '{name}'. functions defined with FN: {sorted(fns)}")
        if name in stack or len(stack) >= MAX_FN_DEPTH:
            raise MoteError(f"function '{name}' calls itself (functions cannot call themselves)")
        params, body = fns[name]
        args = split_top(argtext, ",") if argtext.strip() else []
        if len(args) != len(params):
            raise MoteError(f"{name}: needs {len(params)} argument(s) ({', '.join(params) or 'none'}), got {len(args)}")
        smap = dict(zip(params, args))
        for b in body:
            out.extend(_expand([_replace_params(b, smap)], fns, stack + (name,)))
    return out


def parse(program):
    """Program text -> plan. Done once, before any data is touched.
    Plan items: ("stmt", text) for I(...) / +name(...) / -name, or ("pipe", save_name, [(sym, arg_text), ...])."""
    plan, fns = [], {}
    for line in [l.strip() for l in program.strip().splitlines() if l.strip()]:
        fm = FN_LINE.match(line)
        if fm:
            params = [p.strip() for p in split_top(fm.group(2), ",")] if fm.group(2).strip() else []
            if not all(re.fullmatch(r"[A-Za-z_]\w*", p) for p in params) or len(set(params)) != len(params):
                raise MoteError(f"FN {fm.group(1)}: parameters must be unique plain names")
            if fm.group(1) in fns:
                raise MoteError(f"function '{fm.group(1)}' is defined twice")
            fns[fm.group(1)] = (params, split_top(fm.group(3), "|"))
            continue
        if re.match(r"^(I\(.*\)|\+\w+\(.*\)|-\w+)$", line):
            plan.append(("stmt", line))
            continue
        name = None
        m = re.match(r"^(\w+)\s*=\s*([@a-z_].*)$", line)
        if m:
            name, line = m.group(1), m.group(2)
        steps = []
        for s in _expand(split_top(line, "|"), fns):
            m = STEP.match(s.strip())
            if not m:
                raise MoteError(f"cannot read step '{s}'")
            steps.append((m.group(1), m.group(2) if m.group(2) is not None else ""))
        plan.append(("pipe", name, steps))
    return plan


def _fields(df, fields, step):
    for f in fields:
        if f not in df.columns:
            raise MoteError(f"{step}: unknown field '{f}'. fields: {sorted(df.columns)}")


def _need_table(x, step):
    if not isinstance(x, pd.DataFrame):
        raise MoteError(f"{step}: needs a table as input, got a single value")


def _operand(tok, df):
    tok = tok.strip()
    if tok[0] in "\"'" and tok[-1] == tok[0]:
        return tok[1:-1]
    try:
        return NUM(tok)
    except ValueError:
        if re.fullmatch(r"\w+", tok):
            _fields(df, [tok], "F")
            return df[tok]
        return mote_expr.evaluate(tok, df, _complete)


def _mask(groups, df):
    out = None
    for terms in groups:
        g = None
        for f, op, rhs in terms:
            _fields(df, [f], "F")
            a, b = df[f], _operand(rhs, df)
            if op in ("~", "!~"):
                hit = a.astype(str).str.lower().str.contains(str(b).lower(), regex=False)
                r = hit if op == "~" else ~hit
            else:
                r = {"==": lambda: a == b,
                     "!=": lambda: (a != b) & a.notna() if pd.api.types.is_numeric_dtype(a) else a != b, ">=": lambda: a >= b,
                     "<=": lambda: a <= b, ">": lambda: a > b, "<": lambda: a < b}[op]()
            g = r if g is None else (g & r)
        out = g if out is None else (out | g)
    return out


def _scalar(v):
    return v.item() if hasattr(v, "item") else v


def _load(src):
    """Source -> table. Blank TEXT cells become empty text "" so F(f!="") can drop them."""
    df = src.copy(deep=False) if isinstance(src, pd.DataFrame) else pd.DataFrame(src)
    for c in df.columns:
        if (pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object) and df[c].isna().any():
            df = df.assign(**{c: df[c].fillna("")})
    return df


def _complete(df, fields, step):
    """A blank NUMBER in a calculation is an error, never silently skipped."""
    for f in fields:
        if df[f].isna().any():
            raise MoteError(f"{step}: field '{f}' has blank values. Keep only rows that have one first, "
                            f"e.g. F({f}>-1000000000) (a number test drops blank rows)")


def _literal(name, v):
    if isinstance(v, pd.DataFrame):
        raise MoteError(f"${name} is a table, not a single value")
    if isinstance(v, str):
        if '"' not in v:
            return '"' + v + '"'
        if "'" not in v:
            return "'" + v + "'"
        raise MoteError(f"${name} contains both kinds of quote, so it cannot be used as text")
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    return repr(float(v)) if isinstance(v, (float, np.floating)) else repr(int(v))


def _subst(text, env):
    """Replace $name by the saved single value (outside quotes)."""
    def go(seg):
        def rep(m):
            if m.group(1) not in env:
                raise MoteError(f"unknown variable ${m.group(1)}. saved so far: {sorted(k for k, v in env.items() if not isinstance(v, pd.DataFrame) and not isinstance(v, list))}")
            return _literal(m.group(1), env[m.group(1)])
        return re.sub(r"\$([A-Za-z_]\w*)", rep, seg)
    return _outside_quotes(text, go)


def _step(sym, arg, cur, env, first):
    if "$" in arg:
        arg = _subst(arg, env)
    if sym.startswith("@"):
        if not first:
            raise MoteError(f"{sym}: a source may only start a line")
        if sym[1:] in mote_flow.SOURCES and sym[1:] not in env:
            return _load(mote_flow.source(sym[1:], split_top(arg, ",") if arg.strip() else [], getattr(_tl, "base", None)))
        if sym[1:] not in env:
            raise MoteError(f"{sym}: no such source. have: {sorted(env)}")
        return _load(env[sym[1:]])
    if first:
        raise MoteError(f"line must start with a source like @name, got '{sym}'")
    args = split_top(arg, ",") if arg.strip() else []

    if sym == "F":
        _need_table(cur, "F")
        return cur[_mask(parse_cond(arg), cur)]
    if sym == "P":
        _need_table(cur, "P")
        pairs = [tuple(p.strip() for p in a.split("=", 1)) if "=" in a else (a, a) for a in args]
        _fields(cur, [o for _, o in pairs], "P")
        if len({n for n, _ in pairs}) != len(pairs):
            raise MoteError("P: two fields would get the same name")
        out = cur[[o for _, o in pairs]].copy()
        out.columns = [n for n, _ in pairs]
        return out
    if sym in ("O", "D"):
        _need_table(cur, sym)
        if len(args) != 1:
            raise MoteError(f"{sym}: needs exactly one field")
        _fields(cur, args, sym)
        return cur.sort_values(args[0], ascending=(sym == "O"), kind="stable")
    if sym == "T":
        _need_table(cur, "T")
        if len(args) != 1 or not args[0].isdigit():
            raise MoteError("T: needs one whole number")
        return cur.head(int(args[0]))
    if sym == "U":
        _need_table(cur, "U")
        _fields(cur, args, "U")
        return cur.drop_duplicates(subset=args, keep="first")
    if sym == "A":
        _need_table(cur, "A")
        out = cur
        for part in split_top(arg, ","):
            if "=" not in part or part.split("=")[0].strip() == "":
                raise MoteError("A: needs new=expression")
            new, expr = [p.strip() for p in part.split("=", 1)]
            if new in out.columns:
                raise MoteError(f"A: field '{new}' already exists")
            out = out.assign(**{new: mote_expr.evaluate(expr, out, _complete)})
        return out
    if sym == "Q":
        _need_table(cur, "Q")
        return cur[~_mask(parse_cond(arg), cur)]
    if sym == "Y":
        _need_table(cur, "Y")
        if not 1 <= len(args) <= 2:
            raise MoteError("Y: needs (field=expression) or (field=expression,condition)")
        m = re.match(r"^\s*(\w+)\s*=(?!=)\s*(.+)$", args[0])
        if not m:
            raise MoteError("Y: needs (field=expression) or (field=expression,condition)")
        field, expr = m.group(1), m.group(2)
        if field not in cur.columns:
            raise MoteError(f"Y: unknown field '{field}'. fields: {sorted(cur.columns)} (add a new field with A first)")
        vals = mote_expr.evaluate(expr, cur, _complete)
        old_num = pd.api.types.is_numeric_dtype(cur[field])
        if old_num != pd.api.types.is_numeric_dtype(vals):
            raise MoteError(f"Y: field '{field}' holds {'numbers' if old_num else 'text'}; "
                            f"give {'a number' if old_num else 'text in quotes'}")
        mask = _mask(parse_cond(args[1]), cur) if len(args) == 2 else pd.Series(True, index=cur.index)
        out = cur.copy()
        out[field] = out[field].where(~mask, vals)
        return out
    if sym == "Z":
        _need_table(cur, "Z")
        if not 1 <= len(args) <= 2 or not NAME.match(args[0]) or (len(args) == 2 and args[1] not in ("replace", "add")):
            raise MoteError("Z: needs (name), (name,replace) or (name,add)")
        name, mode = args[0], (args[1] if len(args) == 2 else "create")
        have = name in env
        if mode == "create" and have:
            raise MoteError(f"Z: table '{name}' already exists. Use Z({name},replace) to overwrite (a backup is kept) "
                            f"or Z({name},add) to append")
        if mode == "add":
            if not have:
                raise MoteError(f"Z: table '{name}' does not exist yet. Use Z({name}) to create it")
            base = _load(env[name])
            if set(base.columns) != set(cur.columns):
                raise MoteError(f"Z: both tables need the same fields. {name}: {sorted(base.columns)} new rows: {sorted(cur.columns)}")
            final = pd.concat([base, cur[list(base.columns)]], ignore_index=True)
        else:
            final = cur.reset_index(drop=True)
        _fx().append({"op": "add" if mode == "add" else ("replace" if have else "create"), "name": name,
                         "before": len(env[name]) if have else 0, "df": final})
        env[name] = final
        return final
    if sym == "J":
        _need_table(cur, "J")
        if len(args) != 2 or not args[0].startswith("@"):
            raise MoteError("J: needs (@source,keyfield)")
        if args[0][1:] not in env:
            raise MoteError(f"J: no such source {args[0]}. have: {sorted(env)}")
        right, key = _load(env[args[0][1:]]), args[1]
        _fields(cur, [key], "J")
        _fields(right, [key], "J")
        right = right.drop_duplicates(subset=[key], keep="last")
        extra = [c for c in right.columns if c != key]
        clash = set(extra) & set(cur.columns)
        if clash and len(cur):
            hit = cur[key].isin(right[key]).any()
            if hit:
                raise MoteError(
                    f"J: fields {sorted(clash)} exist on both sides. Trim the right source first, "
                    f"e.g. x=@y|P({key},fieldyouneed) then J(@x,{key})")
        return cur.merge(right, on=key, how="inner", sort=False)
    if sym == "B":
        _need_table(cur, "B")
        if len(args) != 2 or not args[1].isdigit():
            raise MoteError("B: needs (keyfield,n) e.g. B(team,2)")
        _fields(cur, [args[0]], "B")
        return cur.groupby(args[0], sort=False).head(int(args[1]))
    if sym == "K":
        _need_table(cur, "K")
        if len(args) != 1:
            raise MoteError("K: needs one field")
        _fields(cur, args, "K")
        if "cum" in cur.columns:
            raise MoteError("K: field 'cum' already exists")
        _complete(cur, args, "K")
        return cur.assign(cum=cur[args[0]].cumsum())
    if sym == "E":
        _need_table(cur, "E")
        if len(args) != 1 or not args[0].startswith("@"):
            raise MoteError("E: needs (@source)")
        if args[0][1:] not in env:
            raise MoteError(f"E: no such source {args[0]}. have: {sorted(env)}")
        right = _load(env[args[0][1:]])
        if len(cur) and len(right) and set(cur.columns) != set(right.columns):
            raise MoteError(f"E: both tables need the same fields. left: {sorted(cur.columns)} right: {sorted(right.columns)}")
        return pd.concat([cur, right[list(cur.columns)] if len(cur) else right], ignore_index=True)
    if sym == "L":
        _need_table(cur, "L")
        if len(args) != 3 or not args[0].startswith("@"):
            raise MoteError("L: needs (@source,numericfield,tolerance) e.g. L(@bank,amt,0.01)")
        if args[0][1:] not in env:
            raise MoteError(f"L: no such source {args[0]}. have: {sorted(env)}")
        right, key = _load(env[args[0][1:]]), args[1]
        try:
            tol = float(args[2])
        except ValueError:
            raise MoteError("L: tolerance must be a number")
        _fields(cur, [key], "L")
        _fields(right, [key], "L")
        _complete(cur, [key], "L")
        _complete(right, [key], "L")
        pairs = match_tol(cur[key].tolist(), right[key].tolist(), tol)
        extra = [c for c in right.columns if c != key]
        clash = set(extra) & set(cur.columns)
        if clash and pairs:
            raise MoteError(
                f"L: fields {sorted(clash)} exist on both sides. Trim the right source first, "
                f"e.g. x=@y|P({key},fieldyouneed) then L(@x,{key},{args[2]})")
        li, ri = [p[0] for p in pairs], [p[1] for p in pairs]
        return pd.concat([cur.iloc[li].reset_index(drop=True), right.iloc[ri][extra].reset_index(drop=True)], axis=1)
    if sym == "W":
        _need_table(cur, "W")
        if len(args) != 2 or not args[0].startswith("@"):
            raise MoteError("W: needs (@source,keyfield)")
        if args[0][1:] not in env:
            raise MoteError(f"W: no such source {args[0]}. have: {sorted(env)}")
        right = _load(env[args[0][1:]])
        _fields(cur, [args[1]], "W")
        _fields(right, [args[1]], "W")
        return cur[~cur[args[1]].isin(right[args[1]])]
    if sym == "H":
        _need_table(cur, "H")
        if len(args) != 2 or not args[1].isdigit():
            raise MoteError("H: needs (field,decimals) e.g. H(v,1)")
        _fields(cur, [args[0]], "H")
        if "pct" in cur.columns:
            raise MoteError("H: field 'pct' already exists")
        _complete(cur, [args[0]], "H")
        total = cur[args[0]].sum()
        if total == 0:
            raise MoteError("H: column total is zero")
        d = int(args[1])
        return cur.assign(pct=[round(x / total * 100, d) for x in cur[args[0]].tolist()])
    if sym == "G":
        _need_table(cur, "G")
        if len(args) < 2:
            raise MoteError("G: needs (keyfield,AGG:field) e.g. G(cat,S:amt)")
        key, specs = args[0], []
        for raw in args[1:]:
            name = None
            if "=" in raw:
                raw, name = [p.strip() for p in raw.rsplit("=", 1)]
                if not NAME.match(name):
                    raise MoteError(f"G: '{name}' is not a plain field name")
            a = raw.split(":")
            if (a[0] not in G_AGG and a[0] != "C") or (a[0] != "C" and len(a) != 2):
                raise MoteError("G: second arg like S:amt (S sum, C count, V avg, X max, N min, M median, SD std dev, VR variance)")
            specs.append((a, name))
        if len(specs) > 1 and any(n is None for _, n in specs):
            raise MoteError("G: with several totals, name each one, e.g. G(k,S:amt=total,C=n)")
        _fields(cur, [key] + [a[1] for a, _ in specs if len(a) == 2], "G")
        for a, _ in specs:
            if len(a) == 2:
                _complete(cur, [a[1]], "G")
        g = cur.groupby(key, sort=False)
        cols = {}
        for a, name in specs:
            v = g.size() if a[0] == "C" else g[a[1]].agg(G_AGG[a[0]])
            if a[0] in ("SD", "VR") and v.isna().any():
                raise MoteError(f"G: {a[0]} needs at least 2 rows in every group")
            if (name or "v") in cols or (name or "v") == key:
                raise MoteError(f"G: the name '{name or 'v'}' is used twice (or is the key field)")
            cols[name or "v"] = v
        return pd.DataFrame(cols).reset_index()
    if sym in SCALARS:
        _need_table(cur, sym)
        if sym == "C":
            return len(cur)
        want = 2 if sym in ("PC", "CO") else 1
        if len(args) != want:
            raise MoteError(f"{sym}: needs {'(field,percent)' if sym == 'PC' else '(field,field)' if sym == 'CO' else 'one field'}")
        _fields(cur, [args[0]] if sym == "PC" else args, sym)
        if len(cur) == 0:
            raise MoteError(f"{sym}: no rows to {sym}")
        _complete(cur, [args[0]] if sym == "PC" else args, sym)
        c = cur[args[0]]
        if sym in ("SD", "VR", "CO") and len(cur) < 2:
            raise MoteError(f"{sym}: needs at least 2 rows")
        if sym == "PC":
            try:
                p = float(args[1])
            except ValueError:
                p = -1
            if not 0 <= p <= 100:
                raise MoteError("PC: percent must be a number from 0 to 100, e.g. PC(amt,90)")
            return _scalar(np.percentile(c, p))
        if sym == "CO":
            return _scalar(c.corr(cur[args[1]]))
        return _scalar({"S": c.sum, "V": c.mean, "X": c.max, "N": c.min, "M": c.median, "SD": c.std, "VR": c.var}[sym]())
    if sym == "R":
        if isinstance(cur, pd.DataFrame) or len(args) != 1:
            raise MoteError("R: needs a single value and one whole number")
        return round(cur, int(args[0]))
    if sym == "ID":
        _need_table(cur, "ID")
        if len(args) != 1 or not NAME.match(args[0]):
            raise MoteError("ID: needs one new field name, e.g. ID(bank_id)")
        return mote_flow.row_ids(cur, args[0])
    if sym == "RC":
        _need_table(cur, "RC")
        if not 1 <= len(args) <= 3 or not args[0].startswith("@"):
            raise MoteError("RC: needs (@ledger), (@ledger,tolerance) or (@ledger,tolerance,maxsplit), e.g. RC(@invoices)")
        if args[0][1:] not in env or not isinstance(env[args[0][1:]], (pd.DataFrame, list)):
            raise MoteError(f"RC: no such table {args[0]}. have: {sorted(k for k, v in env.items() if isinstance(v, (pd.DataFrame, list)))}")
        try:
            tol = float(args[1]) if len(args) > 1 else 0.01
            split = int(args[2]) if len(args) > 2 else 3
        except ValueError:
            raise MoteError("RC: tolerance must be a number and maxsplit a whole number")
        if split < 2 or split > 4 or tol < 0:
            raise MoteError("RC: maxsplit must be 2 to 4 and tolerance 0 or more")
        return _load(mote_flow.reconcile(cur, _load(env[args[0][1:]]), tol, split))
    if sym in ("WF", "PT"):
        if len(args) != 1:
            raise MoteError({"WF": 'WF: needs one quoted file name, e.g. WF("reports/out.csv")', "PT": 'PT: needs one quoted web address, e.g. PT("http://localhost:8080/report")'}[sym])
        if isinstance(cur, pd.DataFrame) is False and not isinstance(cur, (str, int, float, bool, np.generic)):
            raise MoteError(f"{sym}: nothing to send")
        target = mote_flow.quoted(args[0], f"{sym} target")
        if sym == "WF":
            eff = mote_flow.write_effect(cur, target, getattr(_tl, "base", None))
        else:
            if sum(1 for e in _fx() if e["op"] == "post") >= mote_flow.MAX_POSTS:
                raise MoteError(f"PT: more than {mote_flow.MAX_POSTS} posts in one program")
            eff = mote_flow.post_effect(cur, target)
        _fx().append(eff)
        return f"{'write file' if sym == 'WF' else 'post to'} {eff['name']} ({eff['rows']} {'row' if eff['rows'] == 1 else 'rows'})"
    if sym in ("EX", "RX", "SP", "GT", "LG", "TX", "WK", "LN"):
        _need_table(cur, sym)
        need = {"EX": (1, 1), "RX": (3, 3), "SP": (3, 3), "GT": (2, 3), "LG": (1, 1), "TX": (1, 1), "WK": (2, 2), "LN": (2, 2)}[sym]
        if not need[0] <= len(args) <= need[1]:
            raise MoteError({"EX": "EX: needs one list field, e.g. EX(items)", "RX": 'RX: needs (field,"regex",newfield)',
                             "SP": 'SP: needs (field,"separator",newfield)', "GT": 'GT: needs ("url/{field}",prefix) or ("url/{field}",prefix,tries)',
                             "LG": "LG: needs one field", "TX": "TX: needs one field",
                             "WK": "WK: needs (childlistfield,labelfield), e.g. WK(children,name)",
                             "LN": "LN: needs (pathfield,newfield), e.g. LN(path,line)"}[sym])
        if sym == "EX":
            return _load(mote_flow.explode(cur, args[0]))
        if sym == "RX":
            return _load(mote_flow.regex_rows(cur, args[0], mote_flow.quoted(args[1], "RX pattern"), args[2]))
        if sym == "SP":
            return _load(mote_flow.split_rows(cur, args[0], mote_flow.quoted(args[1], "SP separator"), args[2]))
        if sym == "GT":
            tries = int(args[2]) if len(args) == 3 and args[2].strip().isdigit() else 1
            return _load(mote_flow.fetch_rows(cur, mote_flow.quoted(args[0], "GT address"), args[1], tries))
        if sym == "LG":
            return _load(mote_flow.lag(cur, args[0]))
        if sym == "WK":
            return _load(mote_flow.walk(cur, args[0], args[1]))
        if sym == "LN":
            return _load(mote_flow.file_lines(cur, args[0], args[1], getattr(_tl, "base", None)))
        return mote_flow.text_join(cur, args[0])
    raise MoteError(f"unknown symbol '{sym}'")


def _lit(tok):
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] in "\"'" and tok[-1] == tok[0]:
        return tok[1:-1]
    try:
        return NUM(tok)
    except ValueError:
        raise MoteError(f"cannot read value '{tok}': use a number or quoted text")


def _statement(text, env):
    """I(name,f1,f2)  create empty table | +name(v1,v2)  insert one row | -name  delete a table."""
    m = re.match(r"^I\((.*)\)$", text)
    if m:
        parts = split_top(m.group(1), ",")
        if len(parts) < 2 or not all(NAME.match(p) for p in parts) or len(set(parts[1:])) != len(parts) - 1:
            raise MoteError("I: needs (tablename,field1,field2,...) with unique plain names")
        name, fields = parts[0], parts[1:]
        if name in env:
            raise MoteError(f"I: table '{name}' already exists")
        df = pd.DataFrame({f: pd.Series([], dtype=object) for f in fields})
        _fx().append({"op": "create", "name": name, "before": 0, "df": df})
        env[name] = df
        return f"created table {name} with fields {fields}"
    m = re.match(r"^\+(\w+)\((.*)\)$", text)
    if m:
        name, vals = m.group(1), [_lit(v) for v in split_top(m.group(2), ",")]
        if name not in env:
            raise MoteError(f"+{name}: no such table. have: {sorted(env)}")
        base = _load(env[name])
        if len(vals) != len(base.columns):
            raise MoteError(f"+{name}: needs {len(base.columns)} values in this order: {list(base.columns)}")
        for c, v in zip(base.columns, vals):
            if len(base) and pd.api.types.is_numeric_dtype(base[c]) != (not isinstance(v, str)):
                raise MoteError(f"+{name}: field '{c}' holds {'numbers' if pd.api.types.is_numeric_dtype(base[c]) else 'text'}")
        new = pd.concat([base, pd.DataFrame([vals], columns=base.columns)], ignore_index=True).infer_objects()
        _fx().append({"op": "insert", "name": name, "before": len(base), "df": new})
        env[name] = new
        return f"inserted 1 row into {name} (now {len(new)} rows)"
    name = text[1:]
    if name not in env:
        raise MoteError(f"-{name}: no such table. have: {sorted(env)}")
    _fx().append({"op": "drop", "name": name, "before": len(env[name]), "df": None})
    del env[name]
    return f"dropped table {name}"


def execute(plan, sources, effects=None, base=None):
    """Runs the plan. If `effects` (a list) is given, saved/changed tables are recorded in it for the app to persist."""
    env, result = dict(sources), None
    _tl.fx = effects if effects is not None else []
    _tl.base = base
    try:
        for item in plan:
            if item[0] == "stmt":
                result = _statement(item[1], env)
                continue
            _, name, steps = item
            cur = None
            for i, (sym, arg) in enumerate(steps):
                cur = _step(sym, arg, cur, env, first=(i == 0))
            result = cur
            if name:
                env[name] = cur
    finally:
        _tl.fx = None
    if isinstance(result, pd.DataFrame):
        return result.to_dict("records")
    return result


def run(program, sources, effects=None, base=None):
    return execute(parse(program), sources, effects, base)
