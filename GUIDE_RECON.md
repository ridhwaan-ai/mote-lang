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

Beyond tables. Sources start a line; files stay inside the data folder:
`@json("file")` JSON file as a table (nested objects become fields like customer_name; lists stay lists) | `@text("file")` one row per line (fields n, line) | `@dir("folder")` files (fields name, ext written with the dot like .tmp, size in bytes, mtime = last-modified date YYYY-MM-DD, path for LN) | `@get("url")` or `@get("url",tries)` JSON from a web address | `@const("a","b")` fixed text rows (field line)
`EX(f)` list field -> one row per item (item fields become f_name) | `RX(f,"regex",new)` one row per regex match (first group if any) | `SP(f,"sep",new)` one row per part | `GT("url/{field}",prefix[,tries])` fetch JSON for every row, fields prefix_name | `LG(f)` adds prev_f (previous row's value) | `TX(f)` all values of f joined by newlines as one text | `LN(pathfield,new)` read the text file named in pathfield (e.g. path from @dir): one row per line, in field new | `WK(children,label)` walk a nested tree of any depth: one row per node, parents first, with its own fields plus depth (root is 0), path (labels joined by /) and is_leaf (1 if no children)
Expressions in A and Y also allow `a if cond else b`, `and`, `or`, `not`, comparisons, and `title(s)` `squash(s)` (collapse spaces).
G can total several things and name them: `G(k,C=n,N:d=first,X:d=last,S:amt=total)` (name each one when there are several; a single `G(k,C)` still names it v). Expressions also have `has(s,"word")` (contains, ignores case) for use in `a if has(line,"x") else b`.

Reuse and values. `FN name(p1,p2)=step|step|step` defines a function: a fragment of steps (parameters are replaced by what you pass, outside quotes; give it its own line before use; a function that starts with a source can begin a line). Call it like any step: `name(a,b)`. A function cannot call itself.
A single value saved with `m=@t|V(x)` (any step that gives one value) can be used later as `$m` inside step arguments and expressions, e.g. `F(size>$m)` or `A(z=(x-$m)/$d)`.
Edges: `P(hour=h,n)` renames while projecting; `A(a=x,b=y)` makes several fields in order; `F(size>$m+$d)` can compare against an expression. Programs can also run from a file: python mote_app.py run-file prog.mote --data folder.
Act. `WF("reports/x.csv")` writes the current table (or a single value) to a file inside the data folder; the file type decides the format: .csv, .json, .md (a markdown table `| a | b |` with a `|---|---|` line), .txt (one text value, e.g. from TX). `PT("http://localhost:8080/report")` posts the table as JSON. Put WF or PT last on its line. Nothing is written or posted unless the run is saved (command line: add --apply); without it you only get a preview. Overwritten files are backed up. Script and program file types cannot be written.
Reconcile. `ID(f)` adds a 0-based row-number field f. `RC(@ledger)` (or `RC(@ledger,tol,maxsplit)`, defaults 0.01 and 3) matches the deposits (rows with amount > 0) of the current table, which needs fields description and amount, to the ledger table, which needs reference_number, party_name and amount, in four passes that skip anything already matched: ref_in_description, unique_amount, name_and_amount, split_payment. Rename fields first with P(new=old). It gives one row per match: bank_id (your own bank_id field if the table has one, else the row position), the bank fields, reference_number (several joined by ", "), party_name, match_type, confidence. Matches come in pass order. Unmatched rows are what is left: use W(@matches,bank_id).
Amounts are compared in whole cents, so a payment exactly 1 cent off matches at tolerance 0.01 (comparing floats misses many of these).
