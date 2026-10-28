"""Blank-value rules (v0.4.1): text blanks are "", blank numbers stop the run, days() stops on blank dates."""
import mote, mote_app, mote_fast
S = mote_app.Store("data_trap").sources()
P2 = {"payments": S["payments"].assign(amt=S["payments"].amt.where(S["payments"].pid != "P2"))}
I = 'inv=@invoices|A(invdate=date)|P(ref,invdate)\n'
CASES = [
    (S, I + '@payments|F(date!="")|J(@inv,ref)|A(d=days(invdate,date))|V(d)|R(2)', ("ok", 13.62)),
    (S, I + '@payments|J(@inv,ref)|A(d=days(invdate,date))|V(d)|R(2)', "ERR"),
    (S, '@payments|F(date=="")|P(pid)', ("ok", [{"pid": "P4"}])),
    (S, '@payments|F(date!="")|C', ("ok", 9)),
    (P2, '@payments|S(amt)', "ERR"),
    (P2, '@payments|K(amt)', "ERR"),
    (P2, '@payments|A(x=amt*2)', "ERR"),
    (P2, '@payments|G(payer,S:amt)', "ERR"),
    (P2, '@payments|F(amt>-1000000000)|S(amt)', ("ok", 1345.05)),
    (P2, '@payments|C', ("ok", 10)),
    (P2, '@payments|F(amt<0)|C', ("ok", 2)),
]
bad = 0
for src, prog, exp in CASES:
    try:
        got = ("ok", mote_fast.run(prog, src))
        if isinstance(got[1], float): got = ("ok", round(got[1], 6))
    except mote.MoteError as e:
        got = "ERR"
    ok = (got == exp) if exp != "ERR" else got == "ERR"
    if isinstance(exp, tuple) and isinstance(got, tuple): ok = got[0] == "ok" and got[1] == exp[1]
    bad += not ok
    print(("PASS " if ok else "FAIL ") + prog.replace("\n", " ; ") + ("" if ok else f"  got {got} expected {exp}"))
print(f"\n{len(CASES) - bad} pass, {bad} fail of {len(CASES)}")
