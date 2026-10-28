"""Rough token estimate (a proxy, NOT a real tokenizer): used only to compare sizes."""
import math
import re


def tok(text):
    n = 0
    for m in re.finditer(r"[A-Za-z_]+|\d+|[^\w\s]", text):
        s = m.group(0)
        n += math.ceil(len(s) / 6) if s[0].isalpha() or s[0] == "_" else math.ceil(len(s) / 3) if s.isdigit() else 1
    return n
