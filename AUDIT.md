# Menu Builder — Audit

**Date:** 2026-09-12 · **Scope:** the working tree at `main` `e0a1e3f`
plus its 66 uncommitted files (the whole `DESIGN.md` redesign, Alembic,
`/buscar`, the first-run card — see §D1) · **Lens:** the three modules the
operator named — **Mapeo**, the **insumo form and card**, and
**recetas/preparaciones** — read for *is it correct*, *is it the most
usable version of itself*, and *what would have to change before a second
restaurant*. §E adds the documentation restructuring the operator asked
for, on `restaurant-manager`'s seven-file model.

**Method.** Two halves, as upstream's audit does it.

*Static.* Every template, router and service behind the three modules read
in full, against `PLAN.md`'s decisions and `DESIGN.md`'s stated properties.
Every finding names the file and line it came from.

*Live.* The running stack on this machine (`menu-builder-web-1`, up five
days; `restaurant-manager` answering on `:8000`), read-only SQL against the
production database, every nav page fetched over HTTP and timed, and the
suite run against the working tree inside a throwaway container — the
host has no `python` on `PATH`, which is itself §D5. Nothing was written
to the live database.

Conventions, borrowed from upstream: a fixed finding is struck through with
the date and what fixed it, never deleted; **⟳** marks a finding revised in
a later pass. This is the first pass, so nothing is struck yet. Severity:

| | Meaning |
|---|---|
| **BLOCKER** | Must be resolved before a second customer, or the data is at risk today |
| **HIGH** | Produces a wrong figure, a wrong record, or a dead end the operator cannot get out of |
| **MEDIUM** | Correct today but resting on an unenforced assumption, or a real obstacle to daily use |
| **LOW** | Polish, hygiene, or a bounded inefficiency |

---

## Verdict

**The engine is trustworthy and the front door is not yet walked.** The
pure modules, the costing bridge, the graph guards and the render paths
are as careful as anything in the companion app: 434 tests green in 25 s,
`alembic` at head, money `NUMERIC` end to end, every automatic decision
recorded, and every rule that matters explained at the point where it
would be broken. Nothing in the three modules produces a wrong plate cost
from correct data.

**But the live database says the product has not been used.** After seven
successful syncs (the last four days ago), the mirror holds 1 436 products
and 17 783 purchase lines, and on top of them sit **one insumo, three
confirmed sources, zero recetas, zero histórico rows, zero dismissals and
zero rejections**. Coverage of purchases is 1.7 %. The mapping queue
— the module the operator called the most important, and the one every
other screen is downstream of — has drained three products of 1 433 since
the catalog was rebuilt on 2026-09-06. Every "verified live" figure the
docs lead with (Pollo a la plancha, ₡790.51, 27 histórico points) describes
a database that no longer exists on this machine.

**Three defects in Mapeo would make the first real session go wrong, and
none is caught by the 86 tests behind it.** A factor the operator corrects
on the card is recorded with the *suggestion's* provenance, so the
"¿Por qué este costo?" panel can show `sin_resolver` beside a real number
(§A1). When the suggestion is «nuevo», the card offers no way to pick an
existing insumo, so the second Pechuga becomes `pechuga-deshuesada-2` with
nothing looking wrong (§A2). And there is no per-product «no es insumo»,
so the 36 pending *empaques* and every mis-categorised cloro can only be
paged past, forever, and never leave the badge (§A3). Behind the queue, a
confirmed source **cannot be undone from any screen** — `InsumoFuente.
activo` has no writer (§B1) — which turns each of those three into a
permanent mistake rather than a five-second one.

**The redesign is uncommitted.** 66 files, +8 901/−1 107 lines — every
DESIGN.md step, Alembic, search, the kitchen view, the simulador — sit in
the working tree on a repository synced to OneDrive alongside `.env` and a
live Anthropic key (§D1, §D2). That is the one thing to do before anything
else in this document.

**The four things I would do this week:** commit (§D1); §A1 + a test
(three lines); §B1 (a deactivate/unlink action on the insumo card, which
also closes §A2 and §A3's blast radius); §A3 (a «No es un insumo» button
that writes the `CatalogDescarte` row the table already exists for). Then
sit down with the real queue for one hour and re-read §A before building
anything else in it — every prediction in that section is cheaper to check
against 424 real cards than to argue about.

---

# A. Mapeo — `/insumos/mapeo`

The queue-of-one, the look-alike group, the live cost preview, the
precomputed grouping and the scope-as-setting are all good decisions and
all built to the rule they claim. The findings below are about the
*edges* of that design: what happens when the suggestion is wrong, when
the operator changes something, and when a product should not be there at
all. Those are the cases a triage screen exists for.

**Measured, 2026-09-12.** The queue as the operator would open it today:

| | value |
|---|---|
| pending, in the saved scope (alimentos/bebidas/empaques, 180 días) | **424** |
| pending, whole catalog | 1 433 |
| of the 424, bought once or never | 86 |
| of the 424, in `empaques` | 36 |
| of the 424, matching a cleaning/packaging word in the description | 30 |
| precomputed groupings / pairs | 428 / 1 080 |
| render time, `/insumos/mapeo` (three warm fetches) | **1.27 – 1.93 s** |
| render time, every other page | 0.25 – 0.33 s |

The one-second gap is the `sugerir` call to Claude on every render (§A6).

## A1 · HIGH — A factor the operator edits keeps the suggestion's provenance; a typed factor on an unresolved product is stored as `sin_resolver`

`templates/insumos/mapeo.html:358` posts `factor_origen` as a hidden field
holding `s.factor_origen` — whatever rule produced the *suggested* number.
`routers/mapeo.py:317` passes it through untouched, and
`services/mapeo.py:484` writes it:

```python
fuente.factor_origen = factor_origen if factor is not None else "sin_resolver"
```

Two wrong records follow, and both are reachable on the first card:

- The suggestion read `5000` from the description; the operator corrects
  it to `4540` (the invoice says 4.54 kg). Stored: `4540, descripcion`.
  The panel on `/insumos/{id}` then says the description produced a number
  the description does not contain.
- The suggestion could not read a pack size (`sin_resolver`, factor empty);
  the operator types `1000`. Stored: `1000, sin_resolver` — a resolved
  factor labelled unresolved, and `insumo_costs.explicar` prints exactly
  that in its «Origen» column (`insumos/detail.html:146`).

`manual` — PLAN.md §7.2's rule 1, "a person typed it, never overwritten by
anything below" — is therefore **unreachable from this screen**, which is
the only screen that writes factors. The same hidden field is shared by
every ticked look-alike, so a look-alike's factor (resolved
deterministically at `routers/mapeo.py:168-176`, or typed) is recorded
under the anchor's origin too.

`grep manual tests/test_mapeo.py` returns nothing. The three live sources
happen to be correct (`llm_descripcion`, untouched), which is why nothing
has looked wrong yet.

**Fix.** The server decides provenance, never the form. Post the suggested
factor as `factor_sugerido` (and `factor_sugerido-{pid}` per look-alike);
in `confirmar_muchos`, `origen = suggested_origin if typed == suggested
else "manual"`, per product. One test: *editing the factor records
`manual`*; one more: *typing a factor on a `sin_resolver` product records
`manual`, never `sin_resolver`*.

## A2 · HIGH — When the suggestion is «nuevo», the card cannot point at an existing insumo

`mapeo.html:386-415`: if `s.insumo_id` is set the card shows that insumo's
name and a «No es ese» button; if not, it shows a **text box for a new
name** and nothing else. There is no way to say "this is the Pechuga I
already have". The operator's options are to type a name — which creates a
second row (`unique_slug` quietly hands out `pechuga-deshuesada-2`,
`services/insumos.py:68-84`) — or to page away and hope a later product
suggests the right one.

This is the failure the whole grouping apparatus exists to prevent: a
duplicate insumo splits one ingredient's price history in half, and every
recipe that picks the wrong half is costed on fewer purchases than it has.
`parecidos` guards against it *within the queue*; nothing guards against
it at the boundary between the queue and the insumos that already exist.
With one insumo in the database the deterministic matcher rarely fires
(`mejor_match` needs a 0.35 overlap with a *name*), so today nearly every
card is a «nuevo» card, and the hole is the default path.

**Fix.** A picker on every card, pre-filled with the suggestion when there
is one: upstream's `.msel` **search-combobox variant** is already in this
app's `app.css` (`:466-472`, "the search-combobox variant's real
`<input>`") and unused. Type-to-filter over active insumos, «Crear nuevo:
<name>» as the last row. And a **name-collision hint** on the new-name
path: `similitud(nombre_nuevo, existing)` above ~0.6 renders «Ya existe
«Pechuga deshuesada» — ¿es ese?» with a one-click switch, before Confirmar.

## A3 · HIGH — There is no way to say one product is not an insumo, so the queue cannot reach zero

The bulk gate came off the screen on 2026-09-07 for a good reason
(`routers/mapeo.py:361-384` says it in capitals), and the scope setting
does the family-level job. What went with it is the *per-product* case,
and the live queue has it: 36 pending `empaques` (servilletas, pajillas,
papel aluminio — real costs for a plate that leaves in a box, but not
things anyone will put on a receta line this month), plus whatever
upstream mis-categorises into `alimentos_*`. Today those products can only
be paged past. They never leave the badge, they never leave the
`cobertura` denominator, and «No queda nada por decidir» is unreachable
by construction.

`CatalogDescarte` exists, is tested, has an undo, and has no button.

**Fix.** A third action on the card, ghost, right-aligned with «No es
ese»: **«No es un insumo»**, posting one id to a new
`/insumos/mapeo/descartar-uno` that writes one `CatalogDescarte` row with
its own `lote` and answers with the same flash-and-undo the bulk route
already renders (`mapeo.html:41-51`). Keyboard `X`. The existing «Traer de
vuelta los N descartados» already covers the way back.

## A4 · MEDIUM — Enter inside a numeric field confirms the mapping

`mapeo.html:689-699`: the keydown handler returns early for any input, so
`Enter` in the factor, merma or ventana box falls through to the browser's
implicit submission, and the first submit button in DOM order is
«Confirmar» (`:607`). The factor input's own `hx-post` (`:448-450`) fires
on `keyup … delay:300ms` — so an operator who types `5000` and presses
Enter *to see the cost* writes the mapping instead, with a factor the cost
panel never showed them. The pre-ticked look-alikes (`:568-569`) go with
it.

DESIGN.md §3.7 chose `Enter` = confirm, and outside a field that is right.
Inside a field it is the classic form trap.

**Fix.** In the handler, `Enter` inside an input becomes "blur and let the
preview fire" (`e.preventDefault(); t.blur()`); `Ctrl+Enter` confirms from
anywhere; bare `Enter` with no field focused keeps confirming. Three
lines, and the DESIGN.md sentence gains a clause.

## A5 · MEDIUM — The preview for an existing insumo is labelled and computed in the wrong unit and merma

When the card maps onto an existing insumo the unidad-base select and the
merma field are not rendered (`mapeo.html:419-431, 455-470`), so every
live recompute posts `unidad_base="g"` and `merma_pct=""` to
`/insumos/mapeo/costo` (`routers/mapeo.py:416-424` defaults). For an
insumo costed in `ml` with 12 % merma the panel therefore reads «₡X/g» and
the X has no merma in it — a number that is *not* what confirming will
produce, on the one panel whose stated job is to show that number
(`_costo.html:2-12`). The initial render is right (`_pantalla` uses
`sugerencia.unidad_sugerida`); only the recompute drifts.

Also on this path the card shows the insumo as plain bold text
(`:392`) — no link, no current cost, no unit, no source count. The
operator is asked to confirm "this product is *that*" without being able
to see what *that* is.

**Fix.** Hidden inputs carrying the insumo's `unidad_base` and `merma_pct`
on the existing-insumo branch, and `costo_preview` told which insumo it is
previewing for. Render the destination as a mini insumo card: name as a
link (new tab), «se costea en ml · 12 % merma · 2 fuentes · ₡0.87/ml hoy».

## A6 · MEDIUM — One billed, one-second LLM call per render, uncached, on a screen that re-renders on every keystroke and every page turn

`services/mapeo.py:353` calls `llm_insumo.sugerir` inside `sugerir`, which
`_pantalla` calls on every GET — including each 500 ms-debounced filter
keystroke, each `J`/`K`, the redirect after every confirm, and the
re-render after every «No es ese». The grouping half got a cache
(`CatalogParecido`, 428 anchors precomputed) precisely because "one call
per page turn, paid again every time the operator pages back" was judged
wrong (`:978-982`); the suggestion half did not. Measured: 1.3–1.9 s per
card against 0.25 s for any other page. Over a 424-card session with the
usual back-and-forth that is real money and the single biggest reason the
queue *feels* slow.

The input to the call changes only when the insumo list changes (a
creation, a rename, an archive) or the product's rejections change.

**Fix.** Store the answer beside the grouping: a `catalog_sugerencia` row
keyed on product with `insumos_version` (a hash of `(id, slug, nombre)`
over active insumos plus the product's rejected ids). Recompute when the
hash moves, and let «Agrupar» precompute suggestions in the same
background pass. Same shape as `precomputar_parecidos`, same invalidation
in `confirmar`/`rechazar`.

## A7 · MEDIUM — A ticked look-alike with no factor is confirmed silently as a source that will never contribute

`routers/mapeo.py:168-176` resolves each look-alike's factor
deterministically; when that yields `None` the input renders empty
(`mapeo.html:592`). Nothing marks the row. Confirming writes the source
with `factor_conversion=NULL`, `sin_resolver`, and `insumo_costs` then
excludes it — correctly, and reported on `/insumos/{id}` — but the card
where the decision was made said nothing. A group of five with two blank
factors is an insumo costed on three sources that the operator believes
is costed on five.

**Fix.** The empty box gets `pip--warn` «sin factor» inline, and the
`.parecidos__n` sentence gains a count: «2 de estos no traen factor: se
guardan pero no entran al costo hasta que lo tengan».

## A8 · MEDIUM — `cobertura` measures against a hard-coded 180 days, not the saved scope

`services/mapeo.py:709`: `corte = dt.date.today() - dt.timedelta(days=DIAS_RECIENTE)`.
The queue, the badge and the gate read `alcance(db)`; the headline
percentage — "the only figure that can tell the operator when to stop" —
does not. Set the scope to 90 or 365 days in Ajustes and the bar tracks a
different set from the queue under it. It is also the literal PLAN.md §15
exists to keep out of services, and the same module already took the
trouble of removing it everywhere else.

**Fix.** `cobertura(db)` reads `alcance(db).dias_reciente`; 0 means no
cut-off, as everywhere else.

## A9 · LOW — Queue order is by all-time purchase count, not by purchases inside the scope

`services/mapeo.py:240-256` counts every price point ever. A product
bought 98 times in 2024 and once in 2026 outranks a staple bought weekly
since March. On this catalog (two years, 17 783 lines) the two orders
mostly agree; they diverge exactly on the products that changed supplier,
which are the ones the operator most needs to see grouped. Count within
`dias_reciente` when it is set.

## A10 · LOW — The badge costs two full id-list queries on every page in the app

`_filtros` (`:185`) materialises `_mapeados(db) | _descartados(db)` in
Python and hands the set to `NOT IN (...)`, and `contar_pendientes` runs it
from `templating.pendientes_mapeo` on every render, including `/buscar`
and the kitchen view. Fine at 1 436 products; a literal list of a few
thousand ids in every page's SQL is the kind of thing that turns into a
"why is everything slow" ticket a year in. Two `NOT EXISTS` subqueries
cost nothing to write now.

## A11 · LOW — `factor_origen` reaches the operator as a raw enum on the insumo card

`insumos/detail.html:146` prints `f.fuente.factor_origen` — `llm_descripcion`,
`default_categoria`. The queue card has Spanish labels for the same values
inline (`mapeo.html:436-440`). D3 says every enum gets a label in the
presentation layer; this one needs a `FACTOR_ORIGEN_LABELS` in
`templating.py` beside `METODO_LABELS`, and the card's inline chain should
read from it rather than carrying a second copy.

## A12 · LOW — Nothing in the queue explains what the factor is *per*

The factor is "how many `unidad_base` one `unidad_comercial_base` yields"
— PLAN.md §14's first named risk is getting the denominator wrong. The
card shows the invoice's `unidad_medida` (`mapeo.html:314-324`) and never
`unidad_comercial_base`, the unit the factor is actually defined against.
When those differ (`CJ` vs `BOT`, the case upstream's normalisation exists
for) the operator is typing a number against a unit the screen never
names. The cost panel catches the thousand-fold error; it does not catch
the 24-fold one when the preview happens to be a plausible figure.

**Fix.** The factor field reads as a sentence: «1 **CJ** trae [ 8400 ] ml»
with the purchase unit shown, and «unidad de compra» falling back to
«unidad» when upstream has none.

## The card, as it should read

Everything above, in one drawing. What changes is marked ◆.

```
┌─ Mapeo ─────────────────────────────────────────────────────────────────┐
│ ▓▓▓▓░░░░░░░░░░░░░░░░░░░  4.1 % de las compras · 421 sin decidir         │
│                                                                          │
│ QUESO MOZZARELLA RALLADO LEPRINO 1/15 LB                                 │
│ Proveedor  Mayca      Compras  41     Última  02/09/2026                 │
│ Categoría [Lácteos]   Unidad en factura [Unid]   Fuente [Sugerido por Claude] │
│ ─────────────────────────────────────────────────────────────────────── │
│                                                                          │
│ ◆ Insumo   [ Queso mozzarella           ▾ ]  ← escribí para buscar,      │
│            ┌────────────────────────────────┐   o «Crear nuevo: …»        │
│            │ Queso mozzarella    ₡4.12/g ·2 f│                             │
│            │ Queso parmesano                │                             │
│            │ + Crear nuevo: «Queso mozzar…» │                             │
│            └────────────────────────────────┘                             │
│ ◆          se costea en g · 0 % merma · 2 fuentes · ₡4.12/g hoy  [abrir ↗]│
│                                                                          │
│ ◆ Factor   1 [unidad de compra] trae [ 6804 ] g   [leído por Claude]     │
│            (si lo corregís, queda anotado como «a mano»)                 │
│                                                                          │
│ ╭ Costo ───────────────────────────────────────────────────────────────╮ │
│ │ ₡4.09 /g   Mediana de 38 compras de 90 días ÷ 6804 g por compra      │ │
│ ╰──────────────────────────────────────────────────────────────────────╯ │
│                                                                          │
│ También son este insumo                                                  │
│ [x] QUESO MOZZARELLA LEPRINO 1/2,27 KG  Mayca · 12 compras   Factor [2270] │
│ [ ] QUESO MOZZ RALLADO 1/5 LB           Hevar · 3 compras    Factor [    ] │
│ ◆                                                          ▲ sin factor  │
│ ◆ 1 de estos no trae factor: se guarda, pero no entra al costo.          │
│                                                                          │
│ ◆ [No es un insumo]        [No es ese]            [✓ Confirmar] Ctrl+↵   │
│                                                                          │
│ ‹ Anterior  1 [2] 3 4 … 424  Siguiente ›                     J / K / X   │
└──────────────────────────────────────────────────────────────────────────┘
```

Two more ideas worth trying against the real queue, and deliberately
*not* findings because the honest test is a session, not a reading:

- **A «Confirmar y siguiente» that does not redirect.** Today a confirm
  is a POST, a 303 and a full GET (with §A6's call). With `hx-post` and
  `hx-select="#cola"` — the pattern the filter bar already uses — the
  next card arrives in place and the page strip does not flash. Same
  render path, no second template.
- **Batch the 86 single-purchase products last.** They are 20 % of the
  queue and the least likely to matter; a «solo lo comprado más de una
  vez» chip beside the category picker, defaulting on, would put the
  staples in front. It is a request-scoped filter, not scope, so it
  belongs on the screen (the rule DESIGN.md §3.7 drew).

---

# B. Insumos — the form and the card

`insumos/form.html` is the best-explained form in either app: the card-per-
dimension split, the fold rule (D45), the `<summary>` that carries the
warning. `insumos/detail.html` renders the explanation honestly. The gaps
are around what the card lets the operator *do*.

## B1 · HIGH — A confirmed source cannot be undone, deactivated or re-factored from any screen

`routers/insumos.py` has exactly one mutation past the form: `archivar`
(`:189`). There is no route that touches `InsumoFuente`. `activo` — "a
confirmed source can be switched off without losing the mapping" (PLAN.md
§9.2, and the whole reason `insumo_costs` filters on it) — has **no writer
in the codebase**. `factor_conversion` is written once, in
`mapeo.confirmar`, and never again. A product confirmed onto the wrong
insumo has left the queue (`_mapeados` excludes it) and there is no screen
it can be reached from.

So every mistake §A can produce is permanent: the wrong Pechuga, the
factor typed as 500 instead of 5000, the look-alike ticked in a hurry.
PLAN.md §10.2 specified the fuente row with "an activo toggle"; the table
at `insumos/detail.html:136-154` has six columns and no control.

**Fix.** Three actions per fuente row, all plain forms, all answering the
dual shape: **Desactivar/Reactivar** (`activo`), **Corregir factor**
(inline input, records `manual`), **Quitar** (sets `status='rejected'`
*and* writes an `InsumoMapeoRechazo` so the queue re-offers the product
without re-suggesting this insumo). Each re-costs the card. Non-negotiable
#4 says a human confirms; it has to also mean a human can un-confirm.

## B2 · MEDIUM — Duplicate insumos have no merge

The sibling of §A2. Once `pechuga-deshuesada-2` exists, the only path back
is to archive it — which strands its sources (§B1) and any recipe line
pointing at it. **«Fusionar en…»** on the card: re-point every
`InsumoFuente` and `RecetaLinea` at the survivor, refuse when the two base
units differ in dimension (the same rule `recetas.update` applies to a
yield unit), archive the source row. One service function, one modal,
one test that a receta's cost is unchanged by the merge.

## B3 · MEDIUM — The costing defaults in `app_setting` are dead, and Ajustes cannot change them

`models.py:493-500` carries `metodo_costeo_default`, `ventana_dias_default`,
`upstream_api_url` and `llm_enabled`. `insumo_costs._config` (`:95-106`)
reads them off an `ajustes` argument that **no caller ever passes** — every
`costear`/`explicar` call in the routers omits it, so the fallback
`DEFAULT_METHOD`/`DEFAULT_WINDOW_DAYS` (which mirror `config.py:22-23`)
is the only value ever used. Meanwhile the insumo form's first option reads
«Usar el de Ajustes — Mediana» (`form.html:150`) and `/ajustes` offers no
such control. The operator is pointed at a setting that lives in `.env`
and needs a container restart.

PLAN.md §10 lists «costing defaults» under Ajustes. §15's whole argument
is that these values live in `app_setting`; they do, and nothing reads
them.

**Fix.** `insumo_costs._config` reads `mapeo._fila_ajustes(db)` when
`ajustes` is None (one import, the row is already lazily created); a
«Costeo por defecto» card on `/ajustes` with método and ventana; the env
values become the seed for a fresh row and nothing else. Delete
`llm_enabled` and `upstream_api_url` from the table in the same migration
unless they are about to gain a reader — `llm_enabled` in particular
should be *the* switch for §D7, so keep it and wire it.

## B4 · MEDIUM — Archiving has no warning, and the recipes that use the insumo are a number, not a list

`services/insumos.py:213-218` documents `usage_count` as "the reason an
archive is a warning rather than a silent flip". The button at
`detail.html:14-20` is a bare form with no count near it, no confirm, and
the count itself sits at the very bottom as «Usado en N línea(s) de
receta.» (`:226-229`) — unlinked, contrary to PLAN.md §10.2's "usado en N
recetas as a link". An archived insumo silently disappears from the
palette while every plate that uses it keeps costing (correct) — but the
operator who archived «Pechuga» to tidy the list will not learn that six
plates depend on it until one of them reads `incompleto` for another
reason.

**Fix.** «Usada en» becomes a list of receta links in the subheading
(the builder already does exactly this for preparaciones,
`recetas/detail.html:16-18`); the archive button, when `usos > 0`, opens
upstream's confirm `.sheet` naming them.

## B5 · LOW — The form has no cost preview, and the mapping card does

The card recomputes the cost on every change to factor/merma/método
(`_costo.html`). The insumo form — where an existing insumo's merma and
método actually get *changed* — shows nothing, so «¿qué pasa si le pongo
15 % de merma?» is save, look, edit back. `insumo_costs.explicar` is one
call; a `/insumos/{id}/costo` fragment fed by `hx-post` from the merma,
método and ventana inputs, rendering the same `.costo-prev` block, is the
card's own pattern applied to its own form. For a *new* insumo there is
nothing to preview, and the block should say so rather than show `—`.

## B6 · LOW — The método help text is blank on the default option and never changes

`form.html:176-178` renders `METODO_AYUDA[valores.metodo_costeo]` at
render time. With «Usar el de Ajustes» selected (the default for a new
insumo) the loop matches nothing and the `<p>` is empty; picking another
method changes nothing until the page reloads. Render all six as
`<dl data-metodo=…>` rows and show the one matching the select with three
lines of inline script — or, with no script at all, put the six one-line
descriptions in the fold as a list. The second is simpler and reads fine.

## B7 · LOW — Small things on the form and list

- `categoria_slug` offers every mirrored category, including `limpieza_*`
  and `menaje_*` (`form.html:38-43`). Group with `<optgroup>` by family
  and put the scope's three families first.
- `/insumos` has no «sin fuentes» / «sin costo» / category filter and no
  sort by cost; at forty insumos it is fine, at four hundred it is
  `/buscar`. Cheap now: a `estado`-style select with `sin_fuentes` and
  `sin_costo`, both already computed for the table.
- The merma card's worked example on `detail.html:58-65` is computed in
  the template with `1000 / (1 - merma/100)` — arithmetic in Jinja over a
  Decimal column. It is display-only and correct, but it is a cost figure
  rendered by something other than `costing.py`; move it to a
  `merma_ejemplo` helper so D16's rule has no exception to point at.

## The card, with §B1–B4 applied

```
┌─ Pechuga deshuesada ────────────────────────────── [Editar] [Archivar] ┐
│ [Pollo] pechuga-deshuesada · usada en Pollo a la plancha, Cordon bleu  │◆
│                                                                        │
│ ╭ Costo ──────────────────╮ ╭ Cómo se costea ─────────────────────────╮│
│ │ ₡4.36 /g usable         │ │ g · 12 % merma · Mediana (por defecto) ·││
│ │ Mediana de 23 compras   │ │ 90 días (por defecto)                   ││
│ │ de 90 días, 12 % merma  │ ╰─────────────────────────────────────────╯│
│ ╰─────────────────────────╯                                            │
│                                                                        │
│ ¿Por qué este costo?                                                   │
│ Fuentes                                                                │
│ Producto                      Proveedor  Factor    Origen   Compras  Estado           │
│ FILETE PECHUGA DESH MFS 1/5KG Mayca      5000 g    de la descripción 14  [en uso]     │
│ ◆                              [Corregir factor] [Desactivar] [Quitar] │
│ PECHUGA DESHUESADA            Hevar      1000 g    a mano   9   [en uso]│
│ ◆                              [Corregir factor] [Desactivar] [Quitar] │
│ PECHUGA DESH ALTAVISTA 2 KG   Aviocal    —         sin resolver 6  [falta el factor] │
│ ◆                              [Poner factor: ____ g] [Quitar]         │
│                                                                        │
│ ▸ 23 de 29 compras sostienen esta cifra; 6 quedan fuera de la ventana  │
│                                                                        │
│ ◆ [Fusionar en otro insumo…]                                           │
└────────────────────────────────────────────────────────────────────────┘
```

---

# C. Recetas y preparaciones

The builder is the strongest screen in the app and the one the redesign
spent its attention on; D16, D34, D35, D39–D41 all hold as built and as
tested. Nothing here is a wrong number. The findings are about the
*second* half of editing — undoing, annotating, and the edits that today
require leaving the screen.

## C1 · MEDIUM — Deleting a line is one tap, immediate, and unrecoverable

`_cuerpo.html:137-142`: a `btn--danger` trash icon, `hx-post`, no confirm,
no undo. On the tablet the redesign targets it sits 10 px from the reorder
arrows at 44 px tap size. The line's four values (component, cantidad,
unidad, orden) are trivially re-addable, so this needs no confirm dialog —
it needs an **undo**: `eliminar_linea` returns what it deleted,
`_responder` passes it as `deshacer`, and the fragment renders one flash
with a hidden form posting the same four values to `agregar_linea` plus a
`orden` to `reordenar`. No JavaScript, no state, gone on the next swap.

## C2 · MEDIUM — Adding from the palette inserts «1 g» and leaves the operator in the palette

`_paleta.html:29` posts `cantidad=1` in the base unit. Every add is
therefore followed by scrolling to the new last row, clicking into a
`1`, and retyping — for the most frequent action on the most important
screen. Two changes:

- **A default that is usually closer**: 100 for `g`/`ml`, 1 for `unidad`,
  and for a preparación 100 of its yield unit. Still wrong most of the
  time, but a 100 that becomes 180 is one edit; a 1 that becomes 180 is
  the same edit plus the realisation that 1 g of pechuga was costed.
- **Focus lands in the new line's quantity, selected.** htmx honours an
  `autofocus` attribute inside swapped content; `_contexto_builder`
  already knows which line is newest (highest `orden` after an add), so
  the row's `.step__v` gets `autofocus` on that one render. On a tablet
  that also raises the keyboard, which is what the operator was about to
  do anyway.

## C3 · MEDIUM — `RecetaLinea.nota` has no UI, and the kitchen view is where it would pay

The column exists, `agregar_linea`/`actualizar_linea` accept `nota`
(`services/recetas.py:474, 515`), `NodoCocina` could carry it, and no
template renders or edits it. «Pechuga deshuesada — 180 g — *en tiras,
sin piel*» is what a ficha técnica says and what a cook at the pass
actually reads; without it the vista de cocina is a shopping list. A
`<details>`-free pencil toggle on the row revealing a 300-char input
(`hx-post` on change, same route), and one line in `cocina.html` under
the ingredient in `--muted`. Unlike `merma_pct_override` (D7, "UI when a
real case shows up") this one has its case already: D43 shipped the screen
that reads it.

## C4 · LOW — The quantity stepper steps by 1 on lines measured in grams

`routers/recetas.py:562-593` `_con_paso`: `0.1` below 10, else `1`. On «180 g» the
`+` button is 180 → 181, twenty taps to reach 200; the control the
redesign added for tablets is useless on the unit 90 % of lines use.
Step by the unit: `g`/`ml` by 5 (10 above 100), `kg`/`l` by 0.05,
`unidad` by 1, `oz` by 0.5. It is one table in the router, and the
arithmetic stays on the server.

## C5 · LOW — `categoria_menu` is free text with no suggestions, so the board splits on a typo

`recetas/form.html:78-81`: a plain input with a placeholder.
«Entradas» and «entradas» are two groups on the board; so are «Fondos» and
«Fondos ». A `<datalist>` fed from `select distinct categoria_menu`
costs one query and no script, and `validate` should `.strip()` and
title-case-preserve rather than store the raw string (it strips today;
it does not collapse case).

## C6 · LOW — Board group order is arbitrary and moves when a plate is renamed

`routers/recetas.py:58-72` groups in "first-seen order", and
`svc.listar` orders by *plate name* — so the first group is whichever
category contains the alphabetically-first plate. Rename «Arroz con pollo»
to «Pollo con arroz» and «Fondos» jumps below «Bebidas». The comment
defends first-seen over alphabetical on the grounds that a menu's order
"is neither"; true, but the current order is not the menu's either. Sort
groups by name now; a stored order is a Phase 2 question when the board
grows a price column and the operator starts caring how it reads.

## C7 · LOW — Porciones and rendimiento are edited on another page

The totals block shows «Porciones 4» and «Rinde 2 500 g» as read-only KPI
tiles (`_cuerpo.html:248-295`); changing either is Editar → form → save →
back, and both are the single most cost-sensitive numbers on the screen
after the quantities. An inline input in the tile posting to
`/recetas/{id}` through the same `update` validation, re-rendering
`_cuerpo.html`, gives them the same live treatment the lines have. The
yield-dimension guard in `recetas.update` already protects the dangerous
half.

## C8 · LOW — Archiving a preparación in use has no warning

`services/recetas.py:244-252` `set_activo` has no guard; `usos` exists two
functions up and the builder's subheading already lists them. The palette
hides an archived preparación, costing keeps working (right), and the
operator gets no signal. Same treatment as §B4: a confirm naming the
consumers, and «Archivada · usada en Salsa criolla» as a pip on the board
so the dependency is visible from the list.

## C9 · LOW — The vista de cocina has no print stylesheet

`grep "@media print" app.css` → nothing. D43 built the screen for a
tablet on the pass; the same screen is one `@media print{.rail,.head__acts
{display:none}}` away from being the printed ficha técnica every kitchen
tapes to the wall. Cheapest feature in this document.

## C10 · LOW — The simulador moves quantities only

`_cantidades_simuladas` reads `cant-*` and nothing else. Porciones (for a
plato) and rendimiento (for a preparación) are the other two levers, and
Phase 2's *precio sugerido* — which D42 says rides this control — will
need porciones on the same screen. Add both now as two more inputs
through the same `replace()` on the in-memory dataclasses; units can stay
out, since changing a unit is not a "what if".

## C11 · LOW — Two counts that PLAN.md said would be links

`/preparaciones`' «Usada en» column is an integer (`list.html:171`); PLAN
§10 asks for a link "because the first question about a salsa is always
what breaks if you change it". And the palette shows a component's unit
but not its cost, so choosing between two oils is a guess — showing
`₡/ml` there needs `costos()` over sixty insumos per render, which is why
it is not a finding; it becomes one the day a cached cost exists (§B3's
`app_setting` work is where to put a `costo_cache` snapshot beside it).

## The row, with §C1–C4 applied

```
╭ Pollo a la plancha ──────────────────────────────────────────────────╮
│ ◆ ⓘ Se quitó «Sal · 2 g».  [Deshacer]                                │
│                                                                      │
│ ⠿ ■ Pechuga deshuesada          [−] [ 180 ] [+] [g ▾]   ₡784.80  ↑↓🗑 │
│      ₡4.36 por g · ◆ ✎ en tiras, sin piel                            │
│ ⠿ ■ Salsa criolla  [preparación] [−] [  45 ] [+] [g ▾]   ₡132.10  ↑↓🗑 │
│      ▾ De qué está hecho el lote                                     │
│ ⠿ ■ Aceite de oliva             [−] [ 100 ] [+] [ml ▾]  ₡95.00   ↑↓🗑 │◆ (recién agregado: 100, no 1; cursor aquí)
│                                                                      │
│ ┌ soltá acá para agregar ┐                                           │
│                                                                      │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░▒▒  Composición del costo                          │
│                                                                      │
│ Costo total  ₡1 011.90  +₡95.00   ◆ Porciones [ 1 ]   Por porción ₡1 011.90 │
╰──────────────────────────────────────────────────────────────────────╯
```

---

# D. Cross-cutting — correctness, operations, commercial readiness

Kept short where upstream's audit already carries the argument; this app
inherits every one of those findings that is not Gmail-specific, and the
fix for most of them is a port.

## D1 · BLOCKER — Ten days of work are uncommitted, on a synced drive

`git status`: 66 files, +8 901 / −1 107 against `e0a1e3f` ("Step 1.7").
That diff *is* the redesign — every DESIGN.md step, Alembic (D52), the
`/api/v2` move, `/buscar`, `primer_uso`, the simulador, the kitchen view,
the migration files. One `git checkout .`, one mis-aimed `git stash
drop`, one OneDrive conflict-resolution on `app.css` and it is gone; the
only backup is a pg dump from 2026-09-07 that holds none of it (it is
code, not data). Commit today, in the several commits the work naturally
splits into if the log is to stay readable, or in one if it is not.

## D2 · MEDIUM — The repository, `.env` and the database dump are under OneDrive

Same finding as upstream's §B1, smaller blast radius: this repo's `data/`
is empty and the one dump (`backups/menu-20260907-…dump`) carries supplier
prices and no personal data. What is synced is `.env` with a set
`ANTHROPIC_API_KEY`. Move `backups/` out of the tree and treat the key as
disclosed whenever upstream's key is rotated.

## D3 · MEDIUM — `company_name` defaults to this customer's name in `config.py`

`config.py:14`: `company_name: str = "Alimentos y Servicios"`. D25 allows
customer-specific values in `config.py`; it did not mean as the *default*
that a second install inherits when its `.env` omits the line. Default to
an empty string and let the rail render the app name until one is set —
or read it from upstream's `/api/v2/version`, which is where the
restaurant's identity already lives (D26's argument, one step early).

## D4 · MEDIUM — No error pages, and `/salud` cannot say no

`GET /no-existe` → `404 application/json`. There are no exception handlers
in `main.py` and `/salud` (`:55-57`) returns `{"ok": True}` without
touching the database. Upstream fixed both in 0.92 (D56) with a Spanish
error page that extends `base.html` and a health check that runs
`SELECT 1` and reports the stamped revision; the code ports verbatim, and
the "must never need the database" rule for the handler comes with it.

## D5 · MEDIUM — The suite cannot run on the machine that develops it, and nothing runs it automatically

`python` is not on the host's `PATH` (the Windows Store alias answers),
there is no `.venv`, and `.github/` does not exist. The suite ran for this
audit only by bind-mounting `tests/` and `backend/app` into a throwaway
`web` container:

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -T \
  -v "$PWD/tests:/srv/tests" -v "$PWD/backend/app:/srv/app" \
  web sh -c "cd /srv && python -m unittest discover -s tests"
```

That should be in `CLAUDE.md` as the documented command until a venv
exists, and upstream's `tests.yml` (unit job + from-zero migrations job
with `alembic check`) should be ported — the migrations job matters more
here than there, because `tests/dbcase.py` builds from `Base.metadata`
and a migration that drifts from `models.py` is invisible to the suite
(`CLAUDE.md` says so).

## D6 · LOW — Business dates come from libc, eight times

`dt.date.today()` at eight call sites; `America/Costa_Rica` arrives via
`TZ` in Compose and nothing else. Upstream's §A3/D53 shape exactly:
correct today, untested, and `receta_historico`'s own docstring ("Costa
Rica is UTC-6 — a snapshot at 20:00 stamped in UTC lands on tomorrow")
is the bug that will recur if the env var ever goes missing. Port
`services/clock.py` and its structural test.

## D7 · MEDIUM (commercial) — Product descriptions leave the machine on every card, with no switch and no cap

`llm_insumo.sugerir` and `agrupar` send supplier descriptions to Anthropic
whenever a key is set. `AppSetting.llm_enabled` exists and is read
nowhere (§B3); there is no per-day cap, no cost figure on `/ajustes`, and
no line in `README.md` telling the operator what leaves. For this
restaurant that is a choice already made; for a customer it is an
undisclosed egress and an unbooked per-install dependency — upstream's
§D7, verbatim. Wire the switch, count the calls in a small `llm_call`
ledger (or reuse upstream's `job_run` shape), and show «N consultas hoy»
on Ajustes.

## D8 · BLOCKER (before a second customer) — No authentication

Already designed (PLAN.md §16, D26–D28) and correctly held to "same
commit as the `0.0.0.0` bind". Listed so the release blocker list is
complete in one place; nothing to add to the design.

## D9 · LOW — Three columns ship with no reader or no writer

`Insumo.densidad_g_por_ml` (D11, deliberate), `RecetaLinea.merma_pct_override`
(D7, deliberate), `RecetaLinea.nota` (§C3, not deliberate),
`InsumoFuente.activo` (§B1, not deliberate), `AppSetting.llm_enabled` /
`upstream_api_url` / `metodo_costeo_default` / `ventana_dias_default`
(§B3). A `tests/test_models_inventory.py` that lists the columns known to
be intentionally unused, and fails when a column is written nowhere *and*
not on the list, would have caught three of these.

---

# E. Documentation and repository structure

## E1 · What is stale, cited

`README.md` (Spanish, the operator's document) makes six claims the code
no longer supports:

| line | says | reality |
|---|---|---|
| 34 | «la curva del plato ya trae **27 puntos reales**» | 0 rows in `receta_costo_historico`; 0 recetas |
| 73, 79-80 | `/api/v1` | `/api/v2` since 2026-09-08 (`config.py:18`) |
| 196 | «agrupados por sugerencia, para confirmar de a montones» | a queue of one with look-alikes, since step 2.3 |
| 237 | «Cada línea muestra … qué porcentaje del plato representa» | D39 removed the `%` column |
| 361 | «**Sin Alembic.** … `ALTER TABLE` a mano» | Alembic since 2026-09-07 (D52) |
| 305 | «con 291 pruebas» | 434 |

`DESIGN.md:3` still opens «A proposal, not a record. Nothing here is
decided until … moved into `PLAN.md` §17 as D33–D38» while its own §7
says «What this document is now is a record, not a proposal» and §8 holds
D33–D51, which **are not in `PLAN.md` §17** — the decision log jumps from
D32 to D52 with D21 out of sequence. `CLAUDE.md` (770 lines) spends its
first 200 on a dated build narrative («Step 1.4 is done too») that is a
changelog wearing a conventions file's name. There is no `TODO.md`, no
`CHANGELOG.md`, and `PLAN.md` carries no version number, so nothing in
this repo can be cross-referenced the way upstream's «PLAN.md 0.93» can.

## E2 · The seven-file layout, and where today's content goes

Adopt `restaurant-manager`'s model exactly — same file names, same
one-question-per-file rule, same «a fact lives in exactly one of them»
discipline — because D2 and D23 already argue that one operator should
not maintain two sets of habits, and because the next audit of the suite
should be able to walk both repos with one map.

| File | Answers | Lang | What moves in | Target size |
|---|---|---|---|---|
| `README.md` | what it does, how to run it, what it can't do — for the operator | ES | keep; fix §E1; drop every number that expires (test counts, point counts) | ~300 |
| `CLAUDE.md` | how to work here: where code lives, what must never break, how to test and commit | EN | keep Stack, Migraciones, Where things are, Non-negotiables, Conventions, Easy to get wrong, Known debt. **Out:** the Status narrative (lines ~25–200) → `CHANGELOG.md`. **In:** the container test command (§D5), «Los documentos» table, the commit rule | ~400 |
| `PLAN.md` | what each module is for and why — the spec and the decision log | EN | **In:** D33–D51 from `DESIGN.md` §8, verbatim, in order; a `**Version:**` header; §10 corrected to the built IA. **Out:** the `**Status:**` prose | ~1 400 |
| `DESIGN.md` | how it looks and how it words things — rules only | EN | keep §1 (the split), the palette rules, D45's fold rule, §4 touch, §5 motion, the copy conventions from `CLAUDE.md`. **Out:** every «Built 2026-09-0x, and what it cost» paragraph and §0's diagnosis → `CHANGELOG.md`; §8 → `PLAN.md` | ~400 (upstream's is 425) |
| `TODO.md` | designed and unbuilt, decided and not done, deliberately undecided | EN | new: §B1, §B2, §C3, §C7, §C10, §B3's Ajustes card, the per-insumo histórico (PLAN §10.2), the outbound API (§5.4 C), auth (§16), Phase 2 / Phase 3 outlines, Q7–Q9; a «decided against» section (JSON builder endpoint, session state, mass `oz`) | ~250 |
| `CHANGELOG.md` | what changed in each version and why | EN | new: one entry per step 1.0–1.7 and 2.0–2.6, Alembic, `/api/v2`, the forms (D44) — the prose already exists in `CLAUDE.md` and `DESIGN.md` and only needs headings | ~600 |
| `AUDIT.md` | is it correct, consistent, useful, sellable | EN | this file; refreshed by re-auditing, never edited as work lands | — |

**Versioning.** Adopt `PLAN.md` versions as the only version number,
as upstream does. Proposal: **1.0** = "Phase 1 complete, redesign built,
Alembic" — the state this audit describes — with the changelog written
backwards from there as `0.1 … 0.9` for the eight steps and the
redesign, so the existing dated prose has somewhere to live. Every
`CHANGELOG.md` entry from then on is «Changes in 1.x», and the Known-debt
and TODO entries can cite it.

**Two tests to port with the docs**, because a layout nobody enforces
drifts by the second week:

- `tests/test_docs_inventory.py` — every file under `backend/app`, every
  test file and every migration has an entry in `CLAUDE.md`'s "Where
  things are", and every entry names a file that exists.
- A decision-log check: every `D<n>` referenced anywhere in the repo
  exists as a row in `PLAN.md` §17. Today D33–D51 would fail it, which
  is the point.

## E3 · Repository layout, for the same reasons

Small, because this repo is small; listed so it lands with the docs.

- **`backups/` and `.env` out of the OneDrive tree** (§D2). Compose
  already takes `env_file`; point it at the new location.
- **`deploy/compose.prod.yml`**, ported from upstream: no published db
  port, required password, log rotation. This app's compose file still
  ships `menu`/`menu` credentials and a bind-mounted `--reload`.
- **`.github/workflows/tests.yml`** (§D5).
- **`scripts/`** does not exist yet and should not until there is a
  script; upstream's `ops/` vs `oneoff/` split is the rule to follow when
  the first one appears (the first candidate is a `merge_insumos.py`
  before §B2 gets a UI).
- **`models.py` stays one file.** 588 lines and ten tables is well under
  the point at which upstream split theirs (2 831); note it in Known
  debt with that threshold so nobody splits it early.

---

# F. What I would do, in order

Each line is one commit, and the order is chosen so every step leaves
the app strictly more usable than the last.

1. **§D1** — commit the working tree. Then move `backups/` and `.env` out
   of OneDrive (§D2).
2. **§A1** — provenance decided by the server; two tests. Three lines of
   code and the only thing in this list that fixes a wrong *record*.
3. **§B1** — desactivar / corregir factor / quitar on the fuente row.
   This is what makes every later mistake in the queue a five-second one.
4. **§A3** — «No es un insumo» on the card, with the flash-and-undo the
   bulk route already renders.
5. **§A2** — the insumo picker on the card (the `.msel` search-combobox
   already in `app.css`), and the name-collision hint.
6. **§A4, §A5, §A7, §A8** — the four small card fixes, one commit,
   then **one real hour with the 424-card queue** before anything else
   in §A. Write what that hour found into this file as pass 2.
7. **§C1, §C2** — undo on delete; 100-not-1 and focus on add.
8. **§B3** — wire `app_setting`'s defaults and give Ajustes its card;
   wire `llm_enabled` while there (§D7).
9. **§C3, §C9** — the line note and the print stylesheet, together,
   because they are both "the kitchen view becomes a ficha técnica".
10. **§E2** — the documentation split, in one commit, with the two
    inventory tests. Do this before Phase 2 starts adding to
    `CLAUDE.md`'s status narrative.
11. **§D4, §D5, §D6** — the three upstream ports (error pages and
    `/salud`, CI, `clock.py`). Mechanical; batch them.
12. **§A6** — the suggestion cache. Last in §A because it is the biggest
    change and its value should be measured against a queue that has
    actually been used for a week.
13. **§B2, §C4–C8, §C10–C11, §B5–B7** — as they come up, each small
    enough to ride whatever commit is already touching that screen.

Then Phase 2 — which D42, D44 and §C7/§C10 have quietly been preparing
the row and the simulador for.

---

## What this audit did not cover

- `catalog_sync`, `catalog_client`, `cost_chart`, `receta_historico`,
  `busqueda`, `pagination`, `primer_uso` — read for context, not audited.
  The operator named three modules; the rest wait for a second pass.
- `dnd.js` on a real tablet. `tests/manual/dnd.md` is the checklist and
  it was not run.
- The 424 real cards. Every §A prediction about how the queue *feels* is
  a reading of the code and a 1.3 s timing, not a session. §F step 6.
- Nothing outside the checkout. The running container was checked
  against it: the four Mapeo files are identical by hash, and
  `models.py` — edited on 2026-09-08 11:46, after the image was built —
  differs in two docstring comments only (`/api/v1` → `/api/v2`). The
  app being audited is the code being audited.
- Phase 2's design. §C7 and §C10 name where it will land; nothing here
  designs it.
