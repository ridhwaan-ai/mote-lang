"""Mote v0.1 - a tiny pipeline language for AI to write. One meaning per symbol.

Program = lines. Each line: [name=] step | step | step
Last line's result is the output. Steps are checked: unknown fields,
wrong input type, or bad arguments stop the run with a clear error.
"""
import ast
import datetime
import operator
import re


class MoteError(Exception):
    pass

OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    "~": lambda a, b: str(b).lower() in str(a).lower(),
    "!~": lambda a, b: str(b).lower() not in str(a).lower(),
}
COND = re.compile(r"^\s*(\w+)\s*(==|!=|!~|>=|<=|>|<|~)\s*(.+?)\s*$")


def match_tol(lvals, rvals, tol):
    """One-to-one match: each left value takes the first unused right value within +-tol. Returns [(li, ri)]."""
    used, pairs = set(), []
    for li, lv in enumerate(lvals):
        for ri, rv in enumerate(rvals):
            if ri not in used and abs(lv - rv) <= tol:
                used.add(ri)
                pairs.append((li, ri))
                break
    return pairs
ARITH_OK = re.compile(r"^[\w\s\.\+\-\*/\(\)]+$")


def split_top(s, sep):
    parts, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
            cur += ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts]


def is_table(x):
    return isinstance(x, list)


def need_table(x, step):
    if not is_table(x):
        raise MoteError(f"{step}: needs a table as input, got a single value")


def need_fields(rows, fields, step):
    if rows:
        have = set(rows[0])
        for f in fields:
            if f not in have:
                raise MoteError(f"{step}: unknown field '{f}'. fields: {sorted(have)}")


def value(tok, row):
    tok = tok.strip()
    if (tok[0] in "\"'") and tok[-1] == tok[0]:
        return tok[1:-1]
    try:
        return float(tok) if "." in tok else int(tok)
    except ValueError:
        if tok not in row:
            raise MoteError(f"F: unknown field '{tok}'. fields: {sorted(row)}")
        return row[tok]


def parse_cond(text):
    """or-groups split by ^ ; and-terms split by & ."""
    groups = []
    for og in split_top(text, "^"):
        terms = []
        for t in split_top(og, "&"):
            m = COND.match(t)
            if not m:
                raise MoteError(f"F: cannot read condition '{t}'")
            f, op, rhs = m.group(1), m.group(2), m.group(3)
            if op in ("~", "!~") and not (rhs[0] in "\"'" and rhs[-1] == rhs[0] and len(rhs) >= 2):
                raise MoteError(f"F: {op} needs quoted text on the right, e.g. {f}{op}\"abc\"")
            terms.append((f, op, rhs))
        groups.append(terms)
    return groups


def eval_cond(groups, row):
    for terms in groups:
        ok = True
        for f, op, rhs in terms:
            if f not in row:
                raise MoteError(f"F: unknown field '{f}'. fields: {sorted(row)}")
            if not OPS[op](row[f], value(rhs, row)):
                ok = False
                break
        if ok:
            return True
    return False


def arith(expr, row):
    m = re.match(r"^ym\((\w+)\)$", expr)
    if m:
        if m.group(1) not in row:
            raise MoteError(f"A: unknown field '{m.group(1)}'. fields: {sorted(row)}")
        return str(row[m.group(1)])[:7]
    m = re.match(r"^days\((\w+),\s*(\w+)\)$", expr)
    if m:
        for n in m.groups():
            if n not in row:
                raise MoteError(f"A: unknown field '{n}'. fields: {sorted(row)}")
        try:
            a = datetime.date.fromisoformat(str(row[m.group(1)])[:10])
            b = datetime.date.fromisoformat(str(row[m.group(2)])[:10])
        except ValueError:
            raise MoteError("A: days() needs dates written YYYY-MM-DD")
        return (b - a).days
    if not ARITH_OK.match(expr):
        raise MoteError(f"A: bad expression '{expr}'")
    names = set(re.findall(r"[A-Za-z_]\w*", expr))
    for n in names:
        if n not in row:
            raise MoteError(f"A: unknown field '{n}'. fields: {sorted(row)}")
    return _arith(expr, row)


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow}


def _arith(expr, row):
    """Numbers, field names, + - * / % ** and brackets only. Nothing else is evaluated."""
    def go(n):
        if isinstance(n, ast.Expression):
            return go(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.Name) and n.id in row:
            return row[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = go(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = go(n.left), go(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 64:
                raise MoteError("A: exponent too large (limit 64)")
            return _BIN[type(n.op)](a, b)
        raise MoteError(f"A: bad expression '{expr}'")
    try:
        return go(ast.parse(expr.strip(), mode="eval"))
    except SyntaxError:
        raise MoteError(f"A: bad expression '{expr}'")


AGG = {
    "S": lambda v: sum(v), "C": lambda v: len(v),
    "V": lambda v: sum(v) / len(v), "X": lambda v: max(v), "N": lambda v: min(v),
    "M": lambda v: (lambda s, n: s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)(sorted(v), len(v)),
}


def run_step(step, cur, env, first):
    m = re.match(r"^(@\w+|[A-Z])\s*(?:\((.*)\))?$", step.strip())
    if not m:
        raise MoteError(f"cannot read step '{step}'")
    sym, arg = m.group(1), (m.group(2) if m.group(2) is not None else "")
    if sym.startswith("@"):
        if not first:
            raise MoteError(f"{sym}: a source may only start a line")
        name = sym[1:]
        if name not in env:
            raise MoteError(f"{sym}: no such source. have: {sorted(env)}")
        return [dict(r) for r in env[name]] if is_table(env[name]) else env[name]
    if first:
        raise MoteError(f"line must start with a source like @name, got '{step}'")
    args = split_top(arg, ",") if arg.strip() else []

    if sym == "F":
        need_table(cur, "F")
        g = parse_cond(arg)
        return [r for r in cur if eval_cond(g, r)]
    if sym == "P":
        need_table(cur, "P")
        need_fields(cur, args, "P")
        return [{f: r[f] for f in args} for r in cur]
    if sym in ("O", "D"):
        need_table(cur, sym)
        if len(args) != 1:
            raise MoteError(f"{sym}: needs exactly one field")
        need_fields(cur, args, sym)
        return sorted(cur, key=lambda r: r[args[0]], reverse=(sym == "D"))
    if sym == "T":
        need_table(cur, "T")
        if len(args) != 1 or not args[0].isdigit():
            raise MoteError("T: needs one whole number")
        return cur[: int(args[0])]
    if sym == "U":
        need_table(cur, "U")
        need_fields(cur, args, "U")
        seen, out = set(), []
        for r in cur:
            k = tuple(r[f] for f in args)
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out
    if sym == "A":
        need_table(cur, "A")
        if "=" not in arg or arg.split("=")[0].strip() in ("", ):
            raise MoteError("A: needs new=expression")
        new, expr = arg.split("=", 1)
        new = new.strip()
        if cur and new in cur[0]:
            raise MoteError(f"A: field '{new}' already exists")
        return [{**r, new: arith(expr.strip(), r)} for r in cur]
    if sym == "J":
        need_table(cur, "J")
        if len(args) != 2 or not args[0].startswith("@"):
            raise MoteError("J: needs (@source,keyfield)")
        right = env.get(args[0][1:])
        if right is None:
            raise MoteError(f"J: no such source {args[0]}. have: {sorted(env)}")
        key = args[1]
        need_fields(cur, [key], "J")
        need_fields(right, [key], "J")
        idx = {r[key]: r for r in right}
        out = []
        for r in cur:
            if r[key] in idx:
                extra = {k: v for k, v in idx[r[key]].items() if k != key}
                clash = set(extra) & set(r)
                if clash:
                    raise MoteError(
                        f"J: fields {sorted(clash)} exist on both sides. Trim the right source first, "
                        f"e.g. x=@y|P({key},fieldyouneed) then J(@x,{key})")
                out.append({**r, **extra})
        return out
    if sym == "B":
        need_table(cur, "B")
        if len(args) != 2 or not args[1].isdigit():
            raise MoteError("B: needs (keyfield,n) e.g. B(team,2)")
        need_fields(cur, [args[0]], "B")
        seen, out = {}, []
        for r in cur:
            seen[r[args[0]]] = seen.get(r[args[0]], 0) + 1
            if seen[r[args[0]]] <= int(args[1]):
                out.append(r)
        return out
    if sym == "K":
        need_table(cur, "K")
        if len(args) != 1:
            raise MoteError("K: needs one field")
        need_fields(cur, args, "K")
        if cur and "cum" in cur[0]:
            raise MoteError("K: field 'cum' already exists")
        tot, out = 0, []
        for r in cur:
            tot += r[args[0]]
            out.append({**r, "cum": tot})
        return out
    if sym == "E":
        need_table(cur, "E")
        if len(args) != 1 or not args[0].startswith("@"):
            raise MoteError("E: needs (@source)")
        right = env.get(args[0][1:])
        if right is None:
            raise MoteError(f"E: no such source {args[0]}. have: {sorted(env)}")
        if cur and right and set(cur[0]) != set(right[0]):
            raise MoteError(f"E: both tables need the same fields. left: {sorted(cur[0])} right: {sorted(right[0])}")
        return cur + [{k: r[k] for k in (cur[0] if cur else r)} for r in right]
    if sym == "L":
        need_table(cur, "L")
        if len(args) != 3 or not args[0].startswith("@"):
            raise MoteError("L: needs (@source,numericfield,tolerance) e.g. L(@bank,amt,0.01)")
        right = env.get(args[0][1:])
        if right is None:
            raise MoteError(f"L: no such source {args[0]}. have: {sorted(env)}")
        key = args[1]
        try:
            tol = float(args[2])
        except ValueError:
            raise MoteError("L: tolerance must be a number")
        need_fields(cur, [key], "L")
        need_fields(right, [key], "L")
        pairs = match_tol([r[key] for r in cur], [r[key] for r in right], tol)
        out = []
        for li, ri in pairs:
            extra = {k: v for k, v in right[ri].items() if k != key}
            clash = set(extra) & set(cur[li])
            if clash:
                raise MoteError(
                    f"L: fields {sorted(clash)} exist on both sides. Trim the right source first, "
                    f"e.g. x=@y|P({key},fieldyouneed) then L(@x,{key},{args[2]})")
            out.append({**cur[li], **extra})
        return out
    if sym == "W":
        need_table(cur, "W")
        if len(args) != 2 or not args[0].startswith("@"):
            raise MoteError("W: needs (@source,keyfield)")
        right = env.get(args[0][1:])
        if right is None:
            raise MoteError(f"W: no such source {args[0]}. have: {sorted(env)}")
        need_fields(cur, [args[1]], "W")
        need_fields(right, [args[1]], "W")
        gone = {r[args[1]] for r in right}
        return [r for r in cur if r[args[1]] not in gone]
    if sym == "H":
        need_table(cur, "H")
        if len(args) != 2 or not args[1].isdigit():
            raise MoteError("H: needs (field,decimals) e.g. H(v,1)")
        need_fields(cur, [args[0]], "H")
        if "pct" in (cur[0] if cur else {}):
            raise MoteError("H: field 'pct' already exists")
        total = sum(r[args[0]] for r in cur)
        if total == 0:
            raise MoteError("H: column total is zero")
        return [{**r, "pct": round(r[args[0]] / total * 100, int(args[1]))} for r in cur]
    if sym == "G":
        need_table(cur, "G")
        if len(args) != 2:
            raise MoteError("G: needs (keyfield,AGG:field) e.g. G(cat,S:amt)")
        key = args[0]
        a = args[1].split(":")
        if a[0] not in AGG or (a[0] != "C" and len(a) != 2):
            raise MoteError("G: second arg like S:amt (S sum, C count, V avg, X max, N min)")
        need_fields(cur, [key] + ([a[1]] if len(a) == 2 else []), "G")
        buckets = {}
        for r in cur:
            buckets.setdefault(r[key], []).append(r[a[1]] if len(a) == 2 else 1)
        return [{key: k, "v": AGG[a[0]](v)} for k, v in buckets.items()]
    if sym in AGG:
        need_table(cur, sym)
        if sym == "C":
            return len(cur)
        if len(args) != 1:
            raise MoteError(f"{sym}: needs one field")
        need_fields(cur, args, sym)
        vals = [r[args[0]] for r in cur]
        if not vals:
            raise MoteError(f"{sym}: no rows to {sym}")
        return AGG[sym](vals)
    if sym == "R":
        if is_table(cur) or len(args) != 1:
            raise MoteError("R: needs a single value and one whole number")
        return round(cur, int(args[0]))
    raise MoteError(f"unknown symbol '{sym}'")


def run(program, sources):
    env = dict(sources)
    result = None
    for line in [l.strip() for l in program.strip().splitlines() if l.strip()]:
        name = None
        m = re.match(r"^(\w+)\s*=\s*(@.*)$", line)
        if m:
            name, line = m.group(1), m.group(2)
        cur = None
        for i, step in enumerate(split_top(line, "|")):
            cur = run_step(step, cur, env, first=(i == 0))
        result = cur
        if name:
            env[name] = cur
    return result
