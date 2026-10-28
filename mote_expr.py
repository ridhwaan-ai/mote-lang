"""Mote expressions (v0.5): numbers, text and dates, evaluated a whole column at a time.

Used by A(new=expr) and Y(field=expr[,cond]). Only the listed functions are allowed (nothing else can run).
  numbers: + - * / % **  abs round(x,n) floor ceil sqrt pow(x,n) exp log mod(a,b) sign least(a,b) greatest(a,b)
  text:    upper lower trim len left(s,n) right(s,n) replace(s,old,new) cat(a,b,...) num(s)
  dates:   ym(d) year(d) month(d) day(d) weekday(d) addays(d,n) days(d1,d2)      (dates are YYYY-MM-DD text)
"""
import ast
import operator
import re

import numpy as np
import pandas as pd

from mote import MoteError

NUMERIC_BIN = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
               ast.Mod: np.mod, ast.Pow: np.power}
DATE_ERR = "{f}() needs dates written YYYY-MM-DD"


def _col(x, df):
    return x if isinstance(x, pd.Series) else pd.Series([x] * len(df), index=df.index)


def _need_num(x, name):
    if isinstance(x, pd.Series):
        if not pd.api.types.is_numeric_dtype(x) or pd.api.types.is_bool_dtype(x):
            raise MoteError(f"A: {name} needs numbers, got text. Use num(field) to convert text to a number")
    elif isinstance(x, str):
        raise MoteError(f"A: {name} needs numbers, got text")
    return x


def _need_text(x, df):
    return _col(x, df).astype(str)


def _dates(x, df, fname):
    s = _col(x, df).astype(str).str[:10]
    try:
        d = pd.to_datetime(s, format="%Y-%m-%d")
    except (ValueError, TypeError):
        raise MoteError(f"A: {DATE_ERR.format(f=fname)}")
    if d.isna().any():
        raise MoteError(f"A: {DATE_ERR.format(f=fname)} (some are blank; drop them first, e.g. F(date!=\"\"))")
    return d


def _num_fn(fn, name):
    return lambda args, df: fn(*[_need_num(a, name + "()") for a in args])


def _round(args, df):
    x = _need_num(args[0], "round()")
    n = int(args[1]) if len(args) == 2 else 0
    return np.round(x, n)


def _num_conv(args, df):
    try:
        return pd.to_numeric(_need_text(args[0], df).str.strip())
    except (ValueError, TypeError):
        raise MoteError("A: num() found text that is not a number")


CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def _is_text(x):
    return isinstance(x, str) or (isinstance(x, pd.Series) and not pd.api.types.is_numeric_dtype(x))


def _cond(x, what):
    if isinstance(x, bool):
        return x
    if isinstance(x, pd.Series) and pd.api.types.is_bool_dtype(x):
        return x
    raise MoteError(f"A: {what} needs conditions like amt>5 or status==\"open\"")


def _has(a, df):
    if not isinstance(a[1], str):
        raise MoteError('A: has(text,"word") needs quoted text as its second argument')
    return _need_text(a[0], df).str.lower().str.contains(a[1].lower(), regex=False)


FUNCS = {  # name: (min args, max args, function(args, df))
    "abs": (1, 1, _num_fn(np.abs, "abs")), "floor": (1, 1, _num_fn(np.floor, "floor")),
    "ceil": (1, 1, _num_fn(np.ceil, "ceil")), "sqrt": (1, 1, _num_fn(np.sqrt, "sqrt")),
    "exp": (1, 1, _num_fn(np.exp, "exp")), "log": (1, 1, _num_fn(np.log, "log")),
    "sign": (1, 1, _num_fn(np.sign, "sign")), "pow": (2, 2, _num_fn(np.power, "pow")),
    "mod": (2, 2, _num_fn(np.mod, "mod")), "least": (2, 2, _num_fn(np.minimum, "least")),
    "greatest": (2, 2, _num_fn(np.maximum, "greatest")), "round": (1, 2, _round),
    "upper": (1, 1, lambda a, df: _need_text(a[0], df).str.upper()),
    "lower": (1, 1, lambda a, df: _need_text(a[0], df).str.lower()),
    "trim": (1, 1, lambda a, df: _need_text(a[0], df).str.strip()),
    "len": (1, 1, lambda a, df: _need_text(a[0], df).str.len()),
    "left": (2, 2, lambda a, df: _need_text(a[0], df).str[: int(a[1])]),
    "right": (2, 2, lambda a, df: _need_text(a[0], df).str[-int(a[1]):] if int(a[1]) > 0 else _need_text(a[0], df).str[:0]),
    "replace": (3, 3, lambda a, df: _need_text(a[0], df).str.replace(str(a[1]), str(a[2]), regex=False)),
    "cat": (2, 12, lambda a, df: sum((_need_text(x, df) for x in a[1:]), _need_text(a[0], df)) if len(a) > 1 else None),
    "num": (1, 1, _num_conv),
    "has": (2, 2, lambda a, df: _has(a, df)),
    "title": (1, 1, lambda a, df: _need_text(a[0], df).str.title()),
    "squash": (1, 1, lambda a, df: _need_text(a[0], df).str.replace(r"\s+", " ", regex=True).str.strip()),
    "ym": (1, 1, lambda a, df: _col(a[0], df).astype(str).str[:7]),
    "year": (1, 1, lambda a, df: _dates(a[0], df, "year").dt.year),
    "month": (1, 1, lambda a, df: _dates(a[0], df, "month").dt.month),
    "day": (1, 1, lambda a, df: _dates(a[0], df, "day").dt.day),
    "weekday": (1, 1, lambda a, df: _dates(a[0], df, "weekday").dt.strftime("%a")),
    "addays": (2, 2, lambda a, df: (_dates(a[0], df, "addays") + pd.to_timedelta(_need_num(_col(a[1], df), "addays()"), unit="D")).dt.strftime("%Y-%m-%d")),
    "days": (2, 2, lambda a, df: (_dates(a[1], df, "days") - _dates(a[0], df, "days")).dt.days),
}


def _walk(node, df, used):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float, str)):
            raise MoteError("A: only numbers and quoted text are allowed")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in df.columns:
            raise MoteError(f"A: unknown field '{node.id}'. fields: {sorted(df.columns)}")
        used.add(node.id)
        return df[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _need_num(_walk(node.operand, df, used), "-")
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in NUMERIC_BIN:
        a = _need_num(_walk(node.left, df, used), "+ - * / % **")
        b = _need_num(_walk(node.right, df, used), "+ - * / % **")
        with np.errstate(all="ignore"):
            return NUMERIC_BIN[type(node.op)](a, b)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "fill"
            and not node.keywords):
        if len(node.args) != 2 or not isinstance(node.args[0], ast.Name) or not isinstance(node.args[1], ast.Constant):
            raise MoteError("A: fill(field,value) takes a field name and a plain number or quoted text")
        name, v = node.args[0].id, node.args[1].value
        if name not in df.columns:
            raise MoteError(f"A: unknown field '{name}'. fields: {sorted(df.columns)}")
        col = df[name]
        if pd.api.types.is_numeric_dtype(col) != (not isinstance(v, str)):
            raise MoteError(f"A: fill({name},...) needs {'a number' if pd.api.types.is_numeric_dtype(col) else 'quoted text'}")
        return col.fillna(v) if pd.api.types.is_numeric_dtype(col) else col.where(col != "", v)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        f = node.func.id
        if f not in FUNCS:
            raise MoteError(f"A: unknown function '{f}'. functions: {sorted(FUNCS)}")
        lo, hi, fn = FUNCS[f]
        if not lo <= len(node.args) <= hi:
            raise MoteError(f"A: {f}() takes {lo}{'' if lo == hi else f'-{hi}'} argument(s)")
        args = [_walk(a, df, used) for a in node.args]
        for i, a in enumerate(args):          # numeric option arguments must be plain numbers
            if f in ("left", "right", "round") and i == 1 and not isinstance(a, (int, float)):
                raise MoteError(f"A: {f}() needs a plain whole number as its second argument")
        with np.errstate(all="ignore"):
            return fn(args, df)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in CMP:
        a, b = _walk(node.left, df, used), _walk(node.comparators[0], df, used)
        if _is_text(a) != _is_text(b):
            raise MoteError("A: cannot compare numbers with text")
        r = CMP[type(node.ops[0])](a, b)
        if isinstance(node.ops[0], ast.NotEq) and isinstance(a, pd.Series) and not _is_text(a):
            r = r & a.notna()                      # filters never match a blank number
        return r
    if isinstance(node, ast.BoolOp):
        vals = [_cond(_walk(v, df, used), "and/or") for v in node.values]
        out = vals[0]
        for v in vals[1:]:
            out = (out & v) if isinstance(node.op, ast.And) else (out | v)
        return out
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        v = _cond(_walk(node.operand, df, used), "not")
        return (not v) if isinstance(v, bool) else ~v
    if isinstance(node, ast.IfExp):
        c = _cond(_walk(node.test, df, used), "if")
        a, b = _walk(node.body, df, used), _walk(node.orelse, df, used)
        if _is_text(a) != _is_text(b):
            raise MoteError("A: both branches of an if need the same kind (numbers or text)")
        c = pd.Series(c, index=df.index) if isinstance(c, bool) else c
        return _col(a, df).where(c, _col(b, df))
    raise MoteError("A: bad expression (use numbers, fields, + - * / % **, and the listed functions)")


def evaluate(expr, df, complete):
    """Evaluate expr over df. `complete(df, fields, step)` raises if a used number field has blanks."""
    try:
        tree = ast.parse(expr.strip(), mode="eval").body
    except SyntaxError:
        raise MoteError(f"A: bad expression '{expr}'")
    used = set()
    out = _walk(tree, df, used)
    complete(df, [f for f in used if pd.api.types.is_numeric_dtype(df[f])], "A")
    out = _col(out, df)
    if pd.api.types.is_numeric_dtype(out) and not np.isfinite(out.astype(float)).all():
        raise MoteError("A: the calculation gave an invalid number (divide by zero, or sqrt/log of a bad value)")
    return out
