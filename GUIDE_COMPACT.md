# Mote guide
Data pipelines. A line is `@table|step|step...`. The last line is the answer. Save a result with `name=@t|...` then use `@name`.
Steps (f field, c condition, n number, t table):
`F(c)` keep rows. c: `f==v f!=v f>v f<v f>=v f<=v f~"txt" f!~"txt"`; `&` and, `^` or; text in "quotes"
`P(f,f)` keep fields | `A(n=expr)` new field | `O(f)` sort asc | `D(f)` sort desc | `T(n)` first n rows | `U(f)` distinct f
`J(@t,k)` join on k, adds t's fields; stops on a name clash, so first `x=@t|P(k,needed)` | `W(@t,k)` rows whose k is NOT in t
`G(k,AGG:f)` group, gives fields k and v; AGG: S sum, C count (`G(k,C)`), V avg, X max, N min, M median, SD stdev | `H(f,d)` adds field pct = percent of total, d decimals (e.g. `H(v,1)`)
`B(k,n)` first n rows per k | `K(f)` running total in cum | `E(@t)` stack tables | `L(@t,f,tol)` match f within tol, one to one, adds t's fields
Number results: `S(f) V(f) X(f) N(f) M(f) SD(f) VR(f) PC(f,p) CO(f,g)`, `C` count, `R(n)` round
Expressions in A and Y: `+ - * / % **`; `fill(f,v)` replaces blanks in f with v; abs round floor ceil sqrt pow exp log mod sign least greatest; upper lower trim len left right replace cat num; ym year month day weekday addays days(d1,d2)
Change data (preview until saved): `I(t,f1,f2)` new table, `+t(v1,v2)` add row, `-t` drop, `Q(c)` remove rows, `Y(f=expr,c)` update, `Z(t)` save as new, `Z(t,replace)`, `Z(t,add)`
Blank text is "". A blank number in maths or totals stops the run: drop them first with `F(f>-1000000000)`. Sorts are stable. Rounding is half to even.
Errors say how to fix. For details on any symbol run: python mote_app.py help SYMBOL
Example: `@sales|F(status=="paid")|G(cat,S:amt)|D(v)`
Edges: `P(hour=h,n)` renames while projecting; `A(a=x,b=y)` makes several fields in order; `F(size>$m+$d)` can compare against an expression. Programs can also run from a file: python mote_app.py run-file prog.mote --data folder.
