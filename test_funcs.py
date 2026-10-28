"""v0.5 functions: text, dates, maths, statistics. Expected values worked by hand (or by Python's statistics module)."""
import statistics

import mote
import mote_fast

P = [
    {"id": 1, "name": " Ann Lee ", "email": "ANN@X.COM", "joined": "2026-01-31", "score": 3.5, "qty": 4},
    {"id": 2, "name": "bob ray", "email": "bob@y.org", "joined": "2026-02-28", "score": -2.25, "qty": 9},
    {"id": 3, "name": "Cy", "email": "cy@x.com", "joined": "2026-03-15", "score": 10.0, "qty": 16},
]
Q = [{"code": "12"}, {"code": "7.5"}]
BAD = [{"code": "12"}, {"code": "x"}]
SRC = {"p": P, "q": Q, "bad": BAD}
sc = [r["score"] for r in P]
qt = [r["qty"] for r in P]

def col(field, vals): return [{"id": i + 1, field: v} for i, v in enumerate(vals)]

OK = [
    # text
    ('@p|A(n=upper(name))|P(id,n)', col("n", [" ANN LEE ", "BOB RAY", "CY"])),
    ('@p|A(n=trim(name))|A(m=len(n))|P(id,m)', col("m", [7, 7, 2])),
    ('@p|A(d=lower(email))|F(d~"@x.com")|P(id)', [{"id": 1}, {"id": 3}]),
    ('@p|A(f=left(trim(name),3))|P(id,f)', col("f", ["Ann", "bob", "Cy"])),
    ('@p|A(r=right(email,3))|P(id,r)', col("r", ["COM", "org", "com"])),
    ('@p|A(x=replace(email,"@","[at]"))|P(id,x)', col("x", ["ANN[at]X.COM", "bob[at]y.org", "cy[at]x.com"])),
    ('@p|A(c=cat(id,"-",qty))|P(id,c)', col("c", ["1-4", "2-9", "3-16"])),
    ('@q|A(n=num(code))|S(n)', 19.5),
    # dates
    ('@p|A(y=year(joined))|A(m=month(joined))|A(d=day(joined))|P(y,m,d)',
     [{"y": 2026, "m": 1, "d": 31}, {"y": 2026, "m": 2, "d": 28}, {"y": 2026, "m": 3, "d": 15}]),
    ('@p|A(w=weekday(joined))|P(id,w)', col("w", ["Sat", "Sat", "Sun"])),
    ('@p|A(n=addays(joined,30))|P(id,n)', col("n", ["2026-03-02", "2026-03-30", "2026-04-14"])),
    ('@p|A(g=days(joined,addays(joined,10)))|P(id,g)', col("g", [10, 10, 10])),
    ('@p|A(m=ym(joined))|G(m,C)', [{"m": "2026-01", "v": 1}, {"m": "2026-02", "v": 1}, {"m": "2026-03", "v": 1}]),
    # maths
    ('@p|A(a=abs(score))|P(id,a)', col("a", [3.5, 2.25, 10.0])),
    ('@p|A(r=round(score,1))|P(id,r)', col("r", [3.5, -2.2, 10.0])),     # half to even: -2.25 -> -2.2
    ('@p|A(s=sqrt(qty))|P(id,s)', col("s", [2.0, 3.0, 4.0])),
    ('@p|A(p=pow(qty,2))|P(id,p)', col("p", [16, 81, 256])),
    ('@p|A(p=qty**2)|P(id,p)', col("p", [16, 81, 256])),
    ('@p|A(m=mod(qty,5))|P(id,m)', col("m", [4, 4, 1])),
    ('@p|A(m=least(score,0))|P(id,m)', col("m", [0.0, -2.25, 0.0])),
    ('@p|A(m=greatest(score,0))|P(id,m)', col("m", [3.5, 0.0, 10.0])),
    ('@p|A(f=floor(score))|A(c=ceil(score))|P(f,c)', [{"f": 3.0, "c": 4.0}, {"f": -3.0, "c": -2.0}, {"f": 10.0, "c": 10.0}]),
    ('@p|A(s=sign(score))|P(id,s)', col("s", [1.0, -1.0, 1.0])),
    ('@p|A(t=score*2+qty/4-1)|P(id,t)', col("t", [s * 2 + q / 4 - 1 for s, q in zip(sc, qt)])),
    # statistics
    ('@p|SD(score)', statistics.stdev(sc)),
    ('@p|VR(score)', statistics.variance(sc)),
    ('@p|PC(score,50)', 3.5),
    ('@p|PC(score,90)', 8.7),
    ('@p|CO(score,qty)', statistics.correlation(sc, qt)),
    ('@p|G(name,SD:score)', "ERR"),                                        # one row per group -> error, not NaN
]
ERR = [
    '@p|A(x=upper(nope))', '@p|A(x=score+name)', '@p|A(x=foo(1))', '@p|A(x=year(name))', '@p|A(x=left(name,qty))',
    '@p|A(x=qty/0)', '@p|A(x=sqrt(score))', '@p|A(x=log(qty-4))', '@p|A(x=addays(joined,"a"))', '@bad|A(n=num(code))|S(n)',
    '@p|PC(score,150)', '@p|CO(score)', '@p|A(x=__import__("os"))', '@p|A(x=open)', '@p|A(x=len(name,1))',
]


def norm(x):
    if isinstance(x, list):
        return [{k: (round(float(v), 6) if isinstance(v, float) else v) for k, v in r.items()} for r in x]
    return round(x, 6) if isinstance(x, float) else x


bad = 0
for prog, exp in OK:
    try:
        got = norm(mote_fast.run(prog, SRC))
        ok = exp == "ERR" and False or got == norm(exp)
        msg = "" if ok else f"got {got} expected {norm(exp)}"
    except mote.MoteError as e:
        ok, msg = exp == "ERR", ("" if exp == "ERR" else f"STOPPED: {e}")
    bad += not ok
    print(("PASS " if ok else "FAIL ") + prog + (f"\n     {msg}" if msg else ""))
for prog in ERR:
    try:
        r = mote_fast.run(prog, SRC)
        ok, msg = False, f"should have stopped, got {str(r)[:60]}"
    except mote.MoteError as e:
        ok, msg = True, ""
    bad += not ok
    print(("PASS " if ok else "FAIL ") + "(must stop) " + prog + (f"\n     {msg}" if msg else ""))
print(f"\n{len(OK) + len(ERR) - bad} pass, {bad} fail of {len(OK) + len(ERR)}")

# --- fill(field, value) (v0.5.1) ---
F_SRC = {"o": [{"id": 1, "fee": 2.0, "tag": "a"}, {"id": 2, "fee": None, "tag": ""}, {"id": 3, "fee": 1.0, "tag": "c"}]}
fcases = [
    ('@o|A(f=fill(fee,0))|S(f)', 3.0), ('@o|A(f=fill(fee,0))|V(f)', 1.0),
    ('@o|A(f=fill(fee,0)+1)|S(f)', 6.0), ('@o|A(t=fill(tag,"none"))|P(id,t)', [{"id": 1, "t": "a"}, {"id": 2, "t": "none"}, {"id": 3, "t": "c"}]),
    ('@o|A(f=fill(fee,0))|F(f<2)|C', 2),
]
fbad = ['@o|A(f=fill(fee,"x"))', '@o|A(f=fill(tag,0))', '@o|A(f=fill(nope,0))', '@o|A(f=fill(fee+1,0))', '@o|S(fee)', '@o|A(f=fee+1)']
fb = 0
for prog, exp in fcases:
    got = norm(mote_fast.run(prog, F_SRC)); ok = got == norm(exp); fb += not ok
    print(("PASS " if ok else "FAIL ") + prog + ("" if ok else f"  got {got}"))
for prog in fbad:
    try:
        mote_fast.run(prog, F_SRC); fb += 1; print("FAIL (must stop) " + prog)
    except mote.MoteError:
        print("PASS (must stop) " + prog)
print(f"fill: {len(fcases) + len(fbad) - fb} pass, {fb} fail")
