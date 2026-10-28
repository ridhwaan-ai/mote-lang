"""The engine must not use eval/exec/os.environ/shell, and the arithmetic evaluator must refuse anything but maths."""
import glob
import re

import mote

bad = 0


def check(name, ok, detail=""):
    global bad
    bad += not ok
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + detail))


FORBIDDEN = [r"\beval\s*\(", r"\bexec\s*\(", r"os\.environ", r"os\.system", r"shell\s*=\s*True", r"__import__"]
for path in sorted(glob.glob("mote*.py")) + ["dbtools.py", "mote_run.py"]:
    src = open(path, encoding="utf-8").read()
    for pat in FORBIDDEN:
        check(f"{path} has no {pat}", re.search(pat, src) is None)

row = {"a": 3, "b": 4.5}
for e, want in [("a*b+1", 14.5), ("(a+b)**2", 56.25), ("-a+2", -1), ("1e3*a", 3000.0)]:
    check(f"maths {e}", mote._arith(e, row) == want)
for e in ["__import__('os')", "a.__class__", "a if 1 else b", "9**9**9", "a**100", "a;b", "open('x')", "[1]", "a<b", "'x'*a", "lambda: 1"]:
    try:
        mote._arith(e, row)
        check(f"refuses {e}", False)
    except mote.MoteError:
        check(f"refuses {e}", True)
print("\nALL PASS" if not bad else f"\n{bad} FAILED")
