"""Mote Flow (v0.6): sources and steps for work beyond plain tables. Used by the pandas engine (mote_fast).

Sources (start a line):  @json("file")  @text("file")  @dir("folder")  @get("url"[,tries])  @const("a","b",...)
Steps:  EX(f) one row per item of list field f | RX(f,"regex",new) one row per match | SP(f,"sep",new) one row per part
        GT("url/{field}",prefix[,tries]) fetch JSON per row | LG(f) previous row's value | TX(f) join a field into one text value
Files stay inside the data folder; web calls only go to hosts listed in allowed_hosts.txt (default: none).
"""
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from mote import MoteError

MAX_BYTES = 5_000_000
MAX_CALLS = 200
SOURCES = ("json", "text", "dir", "get", "const")


def quoted(a, what="argument"):
    a = a.strip()
    if len(a) >= 2 and a[0] in "\"'" and a[-1] == a[0]:
        return a[1:-1]
    raise MoteError(f"{what} must be quoted text, got {a}")


def inside(base, rel):
    if base is None:
        raise MoteError("file sources need a data folder")
    root = os.path.realpath(base)
    p = os.path.realpath(os.path.join(root, rel))
    if p != root and not p.startswith(root + os.sep):
        raise MoteError(f"path '{rel}' is outside the data folder")
    return p


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "_"))
        else:
            out[key] = v
    return out


def rows_from(obj):
    if isinstance(obj, list):
        return [flatten(x) if isinstance(x, dict) else {"value": x} for x in obj]
    if isinstance(obj, dict):
        return [flatten(obj)]
    return [{"value": obj}]


def frame(rows):
    return pd.DataFrame(rows) if rows else pd.DataFrame()


ALLOWED_HOSTS = []   # hosts web steps may call; none by default


def allowed_hosts():
    """Hosts listed in ALLOWED_HOSTS plus one-per-line entries in allowed_hosts.txt next to this file."""
    hosts = list(ALLOWED_HOSTS)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "allowed_hosts.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            hosts += [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    return hosts


def allowed(url):
    p = urllib.parse.urlparse(url)
    hosts = allowed_hosts()
    if p.scheme not in ("http", "https") or not p.hostname:
        raise MoteError(f"GET: only http and https addresses are allowed, got '{url}'")
    if p.hostname not in hosts:
        raise MoteError(f"GET: host '{p.hostname}' is not allowed. Allowed hosts: {hosts} (list hosts, one per line, in allowed_hosts.txt next to the engine)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could send us to a host that is not on the allowed list, so redirects are never followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url, tries=1):
    allowed(url)
    last = ""
    for i in range(max(1, tries)):
        try:
            with _OPENER.open(url, timeout=10) as r:
                body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise MoteError("GET: reply is too large")
            return json.loads(body)
        except MoteError:
            raise
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.05)
    raise MoteError(f"GET {url} failed after {max(1, tries)} tries: {last}")


def source(name, args, base):
    """args are already split on commas. Returns a DataFrame."""
    if name == "const":
        if not args:
            raise MoteError("@const needs at least one quoted text")
        return pd.DataFrame({"line": [quoted(a) for a in args]})
    if not args:
        raise MoteError(f"@{name} needs a quoted path or url, e.g. @{name}(\"...\")")
    target = quoted(args[0], f"@{name} path")
    if name == "get":
        tries = int(args[1]) if len(args) > 1 and args[1].strip().isdigit() else 1
        return frame(rows_from(fetch(target, tries)))
    p = inside(base, target)
    if name == "json":
        try:
            with open(p, encoding="utf-8") as f:
                return frame(rows_from(json.load(f)))
        except (OSError, ValueError) as e:
            raise MoteError(f"@json: cannot read '{target}': {e}")
    if name == "text":
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            raise MoteError(f"@text: cannot read '{target}': {e}")
        return pd.DataFrame({"n": list(range(1, len(lines) + 1)), "line": lines})
    if name == "dir":
        if not os.path.isdir(p):
            raise MoteError(f"@dir: '{target}' is not a folder")
        root = os.path.realpath(base)
        rows = [{"name": e.name, "ext": os.path.splitext(e.name)[1], "size": e.stat().st_size,
                 "mtime": datetime.date.fromtimestamp(e.stat().st_mtime).isoformat(),
                 "path": os.path.relpath(e.path, root).replace(os.sep, "/")}
                for e in sorted(os.scandir(p), key=lambda e: e.name) if e.is_file()]
        return pd.DataFrame(rows, columns=["name", "ext", "size", "mtime", "path"])
    raise MoteError(f"unknown source @{name}")


def need_new(df, new, step):
    if new in df.columns:
        raise MoteError(f"{step}: field '{new}' already exists")


def explode(df, f):
    if f not in df.columns:
        raise MoteError(f"EX: unknown field '{f}'. fields: {sorted(df.columns)}")
    out, order = [], []
    for rec in df.to_dict("records"):
        v = rec[f]
        if not isinstance(v, list):
            raise MoteError(f"EX: field '{f}' is not a list (EX splits a list field into one row per item)")
        rest = {k: x for k, x in rec.items() if k != f}
        for item in v:
            new = dict(rest)
            if isinstance(item, dict):
                new.update(flatten(item, f + "_"))
            else:
                new[f] = item
            out.append(new)
    return frame(out) if out else pd.DataFrame(columns=[c for c in df.columns if c != f])


def regex_rows(df, f, pattern, new):
    if f not in df.columns:
        raise MoteError(f"RX: unknown field '{f}'. fields: {sorted(df.columns)}")
    need_new(df, new, "RX")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise MoteError(f"RX: bad pattern: {e}")
    out = []
    for rec in df.to_dict("records"):
        for m in rx.finditer(str(rec[f])):
            out.append({**rec, new: m.group(1) if rx.groups else m.group(0)})
    return frame(out) if out else pd.DataFrame(columns=list(df.columns) + [new])


def split_rows(df, f, sep, new):
    if f not in df.columns:
        raise MoteError(f"SP: unknown field '{f}'. fields: {sorted(df.columns)}")
    need_new(df, new, "SP")
    if sep == "":
        raise MoteError("SP: the separator cannot be empty")
    out = [{**rec, new: part} for rec in df.to_dict("records") for part in str(rec[f]).split(sep)]
    return frame(out) if out else pd.DataFrame(columns=list(df.columns) + [new])


def fetch_rows(df, template, prefix, tries):
    for name in re.findall(r"\{(\w+)\}", template):
        if name not in df.columns:
            raise MoteError(f"GT: unknown field '{name}' in the address. fields: {sorted(df.columns)}")
    if len(df) > MAX_CALLS:
        raise MoteError(f"GT: {len(df)} rows would make {len(df)} web calls (limit {MAX_CALLS})")
    out = []
    for rec in df.to_dict("records"):
        url = re.sub(r"\{(\w+)\}", lambda m: urllib.parse.quote(str(rec[m.group(1)]), safe=""), template)
        reply = fetch(url, tries)
        add = {f"{prefix}_{k}": v for k, v in flatten(reply).items()} if isinstance(reply, dict) else {prefix: reply}
        clash = set(add) & set(rec)
        if clash:
            raise MoteError(f"GT: fields {sorted(clash)} already exist. Use a different prefix")
        out.append({**rec, **add})
    return frame(out) if out else df


def lag(df, f):
    if f not in df.columns:
        raise MoteError(f"LG: unknown field '{f}'. fields: {sorted(df.columns)}")
    new = f"prev_{f}"
    need_new(df, new, "LG")
    s = df[f].shift(1)
    if not pd.api.types.is_numeric_dtype(df[f]):
        s = s.fillna("")
    return df.assign(**{new: s})


def text_join(df, f):
    if f not in df.columns:
        raise MoteError(f"TX: unknown field '{f}'. fields: {sorted(df.columns)}")
    return "\n".join(str(v) for v in df[f].tolist())


MAX_NODES = 100_000
MAX_DEPTH = 200


def walk(df, field, label):
    """Tree walk: every row is a root; `field` holds a list of child nodes of the same shape, to any depth.
    One output row per node, parents before children, with the node's own fields plus depth, path (labels joined by /) and is_leaf."""
    if field not in df.columns:
        raise MoteError(f"WK: unknown field '{field}'. fields: {sorted(df.columns)}")
    out, stack = [], []
    for rec in reversed(df.to_dict("records")):
        stack.append((rec, [], 0))
    while stack:
        node, parent, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise MoteError(f"WK: the tree is deeper than {MAX_DEPTH} levels")
        if label not in node:
            raise MoteError(f"WK: a node has no field '{label}' to build the path from. fields: {sorted(k for k in node if k != field)}")
        kids = node.get(field)
        kids = [k for k in kids if isinstance(k, dict)] if isinstance(kids, list) else []
        path = parent + [str(node[label])]
        own = flatten({k: v for k, v in node.items() if k != field})
        clash = {"depth", "path", "is_leaf"} & set(own)
        if clash:
            raise MoteError(f"WK: nodes already have fields {sorted(clash)}; WK adds depth, path and is_leaf")
        own.update(depth=depth, path="/".join(path), is_leaf=0 if kids else 1)
        out.append(own)
        if len(out) > MAX_NODES:
            raise MoteError(f"WK: more than {MAX_NODES} nodes")
        for k in reversed(kids):
            stack.append((k, path, depth + 1))
    return frame(out)


def file_lines(df, f, new, base):
    """For each row, read the text file whose path (inside the data folder) is in field f: one output row per line."""
    if f not in df.columns:
        raise MoteError(f"LN: unknown field '{f}'. fields: {sorted(df.columns)}")
    need_new(df, new, "LN")
    out = []
    for rec in df.to_dict("records"):
        p = inside(base, str(rec[f]))
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError as e:
            raise MoteError(f"LN: cannot read '{rec[f]}': {e}")
        out.extend({**rec, new: line} for line in lines)
    return frame(out) if out else pd.DataFrame(columns=list(df.columns) + [new])


# ---- acting on the world (v0.9): write a file, post to a web address. Recorded as effects; the app does them only when saving. ----
BLOCKED_EXT = {".py", ".pyw", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".exe", ".dll", ".sh", ".lnk", ".msi", ".jar", ".com", ".scr"}
MAX_WRITE = 5_000_000
MAX_POST = 1_000_000
MAX_POSTS = 5


def _py(v):
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and v != v else v


def records(df):
    return [{k: _py(v) for k, v in r.items()} for r in df.to_dict("records")]


def render(cur, path):
    """Table or single value -> text, format chosen by the file extension (.csv .json .md .txt)."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".csv", ".json", ".md", ".txt"):
        raise MoteError(f"WF: the file name must end in .csv, .json, .md or .txt (got '{ext or 'nothing'}')")
    if isinstance(cur, pd.DataFrame):
        rows = records(cur)
        cols = list(cur.columns)
        if ext == ".json":
            return json.dumps(rows, ensure_ascii=False, indent=1), len(rows)
        if ext == ".csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(cols)
            for r in rows:
                w.writerow(["" if r[c] is None else r[c] for c in cols])
            return buf.getvalue(), len(rows)
        if ext == ".md":
            cell = lambda v: "" if v is None else str(v)
            lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(cell(r[c]) for c in cols) + " |" for r in rows]
            return "\n".join(lines), len(rows)
        raise MoteError("WF: a .txt file needs a single text value (use TX(field) first); use .csv, .json or .md for a table")
    v = _py(cur)
    if ext == ".json":
        return json.dumps(v, ensure_ascii=False), 1
    if ext in (".txt", ".md"):
        return str(v), 1
    raise MoteError("WF: a .csv file needs a table")


def write_effect(cur, rel, base):
    p = inside(base, rel)
    if os.path.splitext(p)[1].lower() in BLOCKED_EXT:
        raise MoteError(f"WF: files of this type cannot be written ({os.path.splitext(p)[1]})")
    if "_backups" in os.path.relpath(p, os.path.realpath(base)).replace("\\", "/").split("/"):
        raise MoteError("WF: the _backups folder cannot be written to")
    text, n = render(cur, rel)
    if len(text.encode("utf-8")) > MAX_WRITE:
        raise MoteError(f"WF: the output is larger than {MAX_WRITE} bytes")
    return {"op": "file", "name": os.path.relpath(p, os.path.realpath(base)).replace(os.sep, "/"), "path": p, "text": text, "before": 0, "rows": n, "df": None}


def post_effect(cur, url):
    allowed(url)
    body = json.dumps(records(cur) if isinstance(cur, pd.DataFrame) else _py(cur), ensure_ascii=False)
    if len(body.encode("utf-8")) > MAX_POST:
        raise MoteError(f"PT: the body is larger than {MAX_POST} bytes")
    return {"op": "post", "name": url, "url": url, "body": body, "before": 0, "rows": len(cur) if isinstance(cur, pd.DataFrame) else 1, "df": None}


def do_post(url, body):
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _OPENER.open(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        raise MoteError(f"PT: {url} answered HTTP {e.code}")
    except Exception as e:
        raise MoteError(f"PT: could not reach {url}: {type(e).__name__}: {e}")


# ---- reconciliation (v1.0): four-pass bank-to-invoice matcher as one step -------------------------------------------------
MAX_COMBOS = 3_000_000


def row_ids(df, new):
    need_new(df, new, "ID")
    return df.assign(**{new: range(len(df))})


def reconcile(bank, ledger, tol=0.01, max_split=3):
    """bank needs fields description, amount; ledger needs reference_number, party_name, amount.
    Four passes over the DEPOSITS (amount > 0) in row order, each skipping deposits/invoices already matched:
    1 ref_in_description (exactly one unmatched invoice whose normalised reference is inside the normalised description, and its amount matches)
    2 unique_amount (exactly one unmatched invoice with a matching amount)
    3 name_and_amount (exactly one unmatched invoice with matching amount AND its normalised name inside the description)
    4 split_payment (first group of 2..max_split unmatched invoices, in ledger order, adding up to the deposit).
    Returns one row per match: bank_id (the bank's own bank_id field if it has one, else its row position), the bank row's fields, reference_number, party_name, match_type, confidence."""
    from itertools import combinations
    for tbl, need, label in ((bank, ("description", "amount"), "bank"), (ledger, ("reference_number", "party_name", "amount"), "ledger")):
        miss = [c for c in need if c not in tbl.columns]
        if miss:
            raise MoteError(f"RC: the {label} table needs fields {list(need)} (missing {miss}; fields: {sorted(tbl.columns)}). Rename with P(new=old).")
        if len(tbl) and (not pd.api.types.is_numeric_dtype(tbl["amount"]) or tbl["amount"].isna().any()):
            raise MoteError(f"RC: the {label} 'amount' must be a number in every row")
    clash = {"match_type", "confidence", "reference_number", "party_name"} & set(bank.columns)
    if clash:
        raise MoteError(f"RC: the bank table already has fields {sorted(clash)}; RC adds reference_number, party_name, match_type and confidence")
    has_id = "bank_id" in bank.columns
    if has_id and bank["bank_id"].duplicated().any():
        raise MoteError("RC: the bank_id values you supplied must be unique")
    norm = lambda t: re.sub(r"[^A-Z0-9]", "", str(t).upper())
    brecs = bank.to_dict("records")
    ids_of = [r["bank_id"] if has_id else i for i, r in enumerate(brecs)]
    deps = [(i, r) for i, r in enumerate(brecs) if r["amount"] > 0]
    ndesc = {i: norm(r["description"]) for i, r in deps}
    L = ledger.to_dict("records")
    nref = [norm(r["reference_number"]) for r in L]
    nname = [norm(r["party_name"]) for r in L]
    cents = lambda x: int(round(float(x) * 100))            # amounts are compared in WHOLE CENTS, never as floats (a 1-cent gap must count as 0.01)
    tol_c = round(tol * 100)
    if abs(tol * 100 - tol_c) > 1e-6:
        raise MoteError("RC: the tolerance must be a whole number of cents (0, 0.01, 0.02 ...)")
    lamt = [cents(r["amount"]) for r in L]
    ub, ul, out = set(), set(), []

    def record(i, r, ids, mtype, conf):
        ub.add(i)
        ul.update(ids)
        ids = sorted(ids)
        names = []
        for j in ids:
            if L[j]["party_name"] not in names:
                names.append(L[j]["party_name"])
        out.append({"bank_id": _py(ids_of[i]), **{k: _py(v) for k, v in r.items() if k != "bank_id"}, "reference_number": ", ".join(str(L[j]["reference_number"]) for j in ids),
                    "party_name": ", ".join(str(n) for n in names), "match_type": mtype, "confidence": conf})

    for i, r in deps:                                                           # pass 1
        if i in ub:
            continue
        c = [j for j in range(len(L)) if j not in ul and nref[j] in ndesc[i]]
        if len(c) == 1 and abs(lamt[c[0]] - cents(r["amount"])) <= tol_c:
            record(i, r, [c[0]], "ref_in_description", "high")
    for i, r in deps:                                                           # pass 2
        if i in ub:
            continue
        c = [j for j in range(len(L)) if j not in ul and abs(lamt[j] - cents(r["amount"])) <= tol_c]
        if len(c) == 1:
            record(i, r, [c[0]], "unique_amount", "high")
    for i, r in deps:                                                           # pass 3
        if i in ub:
            continue
        c = [j for j in range(len(L)) if j not in ul and abs(lamt[j] - cents(r["amount"])) <= tol_c and nname[j] and nname[j] in ndesc[i]]
        if len(c) == 1:
            record(i, r, [c[0]], "name_and_amount", "medium")
    for i, r in deps:                                                           # pass 4
        if i in ub:
            continue
        open_items = [j for j in range(len(L)) if j not in ul]
        found = False
        for k in range(2, max_split + 1):
            from math import comb
            if comb(len(open_items), k) > MAX_COMBOS:
                raise MoteError(f"RC: {len(open_items)} open invoices make too many groups of {k} to try (limit {MAX_COMBOS}); lower the split size")
            for combo in combinations(open_items, k):
                if abs(sum(lamt[j] for j in combo) - cents(r["amount"])) <= tol_c:
                    record(i, r, list(combo), "split_payment", "medium")
                    found = True
                    break
            if found:
                break
    cols = ["bank_id"] + [c for c in bank.columns if c != "bank_id"] + ["reference_number", "party_name", "match_type", "confidence"]
    return pd.DataFrame(out, columns=cols)
