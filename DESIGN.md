# DESIGN.md — the UI/UX redesign

A proposal, not a record. Nothing here is decided until it is argued and
moved into `PLAN.md` §17 as D33–D38. `PLAN.md` §10 (information
architecture) stays true; this document replaces §10.1's sketch of the
builder and adds everything §10 never had — a visual system, an
interaction model, and a touch story.

**One thing is not up for discussion.** Every proposal below is a
*rendering* of a number the server already computed. D16 (one costing
engine, no JS copy of the math) is the non-negotiable most at risk from
"make it more interactive", and it survives this document intact.

---

## 0. What is actually wrong

Three failures, independent of each other, and only the first one is the
one that gets noticed.

### 0.1 The mimicry is a re-implementation, not a port

`restaurant-manager/backend/app/static/app.css` is 982 lines.
`menu-builder`'s is 237. The small one is not a subset — it is a second,
incompatible design system that happens to look similar in a screenshot.

**The class vocabulary diverged.** The same components carry different
names in the two apps, so nothing transfers and every fix has to be made
twice:

| upstream | here | what it is |
|---|---|---|
| `.head` / `.head__title` / `.head__acts` | `.stage__head` | page header |
| `.card__h` / `.card__s` | `.card__title` | card heading |
| `.kpi__k` / `.kpi__v` | `.kpi__label` / `.kpi__value` | a KPI tile |
| `.filters` / `.filters__q` | `.toolbar__filters` | filter row |
| `.ta-r` / `.c-muted` | `.num` / `.muted` | alignment, tone |
| `.btn--solid` | *(bare `.btn`)* | primary button |
| `.grid__name` | *(bare `<strong>`)* | identity cell |

**The tokens were re-picked, not copied.** `--paper` and `--surface`
match; nothing else does. `--teal` here is `#0f766e` — that is Tailwind's
`teal-700`, which tells you it was chosen from a swatch list rather than
ported from upstream's `#0d5c4d`. Upstream's three-level text ramp
(`--ink` / `--ink-2` / `--muted`) is collapsed to two (`--ink` /
`--ink-soft`), so tertiary text has nowhere to go. There is no `--line-2`,
no `--teal-ink`, no `--r`, no `--rail-w`, and no `--ui` / `--mono` font
tokens.

The missing `--teal-ink` is why `.btn:hover` is `filter: brightness(1.08)`
— a hover state invented to stand in for a colour that exists upstream.
The missing `--r` is why there are **five** border radii here (6, 8, 10,
12, 999px) against upstream's two (`--r` 7px, and 20px for a pip).

**Things that were simply never written:**

- **No `a { }` rule at all.** Upstream has
  `a{color:var(--teal);text-decoration:none}` plus a hover underline.
  Here, every link in the app renders as default browser blue, underlined
  — the «usada en» links on the builder, the faltantes in the incompleto
  banner, every ingredient name in `_cuerpo.html`. This is the most
  visible break in the app and it is one line of CSS.
- **No global `:focus-visible`.** Upstream has one. Here only `.input`
  has a focus style, so every button and link focuses with the browser
  default ring — which matters more than usual in an app whose whole
  keyboard path (`↑` `↓` `✕` `Aplicar`) is buttons.
- **No `input, select, textarea` base rule.** Upstream styles the bare
  elements; here it is `.input`, so an unclassed control renders native.
- **No `select` arrow replacement.** Upstream draws its own inline-SVG
  triangle so every dropdown matches. Here the unit selector on every
  recipe line is native browser chrome.
- **No icons.** Upstream uses 16px stroked Feather-style SVGs everywhere:
  every rail item, and `.btn--icon` + `.btn--danger` for destructive
  actions (`products/categorias.html` deletes with a real trash icon).
  Here the builder's controls are text glyphs — `⠿` for the drag handle,
  `↑` `↓` for reorder, `✕` for delete — which read as ASCII art next to
  upstream, and `✕` in particular does not say *destructive* the way a red
  trash button does.
- **`.grid` is not a component here.** Upstream's has its own surface,
  border, radius and `overflow:hidden`, and 11px uppercase letter-spaced
  headers. Here it is a bare `<table>` that borrows its box from whatever
  `.card` it was dropped inside, with 12px sentence-case headers.
- **Pips inverted.** Upstream fills meaningful states solid
  (`.pip--warn` = white on `--amber`); here they are soft tints with a
  border, which is upstream's `.pip--faint` treatment applied to states
  that are supposed to be loud.

None of this is a judgement call that went the other way. It is a system
that was described from memory instead of read.

### 0.2 The app is shaped like a ledger, but the work is composition

This is the failure the operator actually feels, and fixing 0.1 does not
touch it.

Five of the six screens are the same template: `.toolbar` with a search
box and a primary button, then a `.card` wrapping a `<table class="grid">`.
That is the correct shape for `restaurant-manager`, which is a record of
things that already happened and that you *read*. It is the wrong shape
for the screen `PLAN.md` §10.1 calls "the screen everything else exists to
serve."

Specifically:

- **The builder is a spreadsheet.** `_cuerpo.html` renders the recipe as
  `<table class="grid lineas">` — the *identical component* as the insumos
  list. The most direct-manipulation surface in the app is styled as a
  read-only data table with form controls stuffed into two of its cells.
- **The most valuable fact on the screen is five characters of grey
  text.** "What is actually expensive here" is what §10.1 says the screen
  is for, and it is delivered as a `%` column in `.muted`. It is not
  sorted, not visualised, not emphasised. You have to read six numbers and
  compare them yourself.
- **The recipe DAG — this app's most distinctive structure — hides behind
  a disclosure triangle.** A preparación's breakdown is a `<details>`
  inside a `<td colspan="5">`. Three-level nests are the real case
  (`fondo oscuro → salsa criolla → plato`, verified live), and the shape
  of that nest is invisible unless you click into it one level at a time.
- **The total changes silently.** htmx swaps all of `#receta-cuerpo` on
  every edit, so `₡1 046` becomes `₡1 088` with no transition, no delta,
  no acknowledgement that anything happened. The app's entire reason to
  exist is that this number responds to you, and the response is
  indistinguishable from a page that did not reload.
- **`/recetas` is a table of a menu.** A menu is the domain object. It has
  categories (`categoria_menu` exists and is rendered as a grey subtitle),
  it has plates that people picture, and it is the thing the operator
  hands to a customer. It renders as seven columns sorted by nothing in
  particular.
- **The mapping queue is an unbounded stack of identical forms.** Against
  a 1 420-product catalog, `mapeo.html` emits one `.card` per pending
  product, each with a full inline form. That is a page that gets longer
  the more work you have, which is exactly backwards.
- **Nothing is reachable except by pointing at it.** There is no search in
  the app at all — no way to find an insumo except by paging `/insumos`,
  which gets worse with every mapping confirmed — and no shortcuts.

### 0.3 Touch is broken, not merely unstyled

The tablet-in-the-kitchen target is not a styling pass. It is a defect:

- **`dnd.js` uses HTML5 drag events exclusively** — `dragstart`,
  `dragover`, `dragleave`, `drop`, `dragend`, plus `draggable="true"` in
  the markup. **None of those fire on a touchscreen.** Drag-and-drop, the
  builder's headline interaction, does not exist on an iPad. The keyboard
  fallback (`↑` `↓`) still works, which is exactly why nobody noticed.
- **Targets are half-size.** A `.pal` palette row is ~31px tall (5px
  padding, 21px line-box); `.btn--xs` is 2px/7px padding; `.input--xs` is
  68px wide with 4px padding. The touch minimum is 44px.
- **Hover-only information.** The "viejo" pip's explanation lives in
  `title="El precio más nuevo tiene N días"` — unreachable by tap.
  Upstream already solved this with its `.date-note[data-tooltip]`
  pattern; it was not ported.
- **One breakpoint in the whole app**, `@media (max-width:900px)` on
  `.builder`. The rail is a fixed 220px flex sibling at every width, so a
  768px portrait tablet gives the actual work 548px.

---

## 1. The organizing idea: the shell is upstream's, the canvas is ours

`restaurant-manager` is an **office**: things that happened, that you read,
reconcile and file. `menu-builder` is a **workshop**: things you make, that
respond while you make them. Same building, different room — and a
workshop furnished like an office is what we have now.

So the redesign splits the app in two, deliberately:

**The shell is upstream's, verbatim.** The rail, the page header, the
lists, the forms, the settings panel, the buttons, the inputs, the pips,
the tables. These are `restaurant-manager` screens and should be
*indistinguishable* from it — same tokens, same class names, same icons.
An operator moving between the two apps should not notice the boundary,
and a fix to a button should be one edit that can be pasted into either
repo. This is D23 (the suite ships together) honoured properly for the
first time.

**The canvas is ours, and owes upstream nothing.** The builder, the
cost-explanation panel, the mapping triage. Upstream has no equivalent of
any of them, so there is nothing to be consistent *with*, and imitating
its table idiom there is what produced 0.2. These get their own language:
larger type, live numbers, direct manipulation, colour that carries data
rather than decorating it.

Two principles to add to `PLAN.md` §2, which the rest of this document is
downstream of:

> **The number moves in front of you.** A figure that changed because of
> something you just did must be visibly different from a figure that was
> already there. D16 guarantees the server computed it; the UI's job is to
> make the change legible.

> **The shape of the recipe is visible without clicking.** Nesting, cost
> share and incompleteness are structural facts about a recipe, and
> structure is drawn, not disclosed.

---

## 2. Phase A — the shell

Boring, large, and it unlocks everything else. No layout changes, no new
screens; the app should look *the same but correct* when this lands.

**A1. Adopt upstream's `app.css` as the baseline.** Copy it into this repo
(per the scope decision: `restaurant-manager` is untouched), drop the
divergent tokens, and keep only the menu-builder-specific sections
(`.builder`, `.pal`, `.linea*`, `.soltar`, `.cost-chart`, `.costo`). Then
rename classes across all 12 templates to upstream's vocabulary. One
mechanical commit, no visual intent, reviewable as a diff.

**Stamp the copy with its origin.** Two files that start identical and are
maintained separately are two files that drift, and the scope decision
(menu-builder only) accepts that. What makes the drift survivable is a
header comment in this app's `app.css` recording the upstream commit it
was ported from, so the next port is `git diff <that sha>..HEAD -- app.css`
in the other repo rather than an archaeology exercise. Update the sha
whenever a port happens.

**A2. The three one-liners that fix the most visible damage:** the global
`a { }` rule, the global `:focus-visible`, and the bare
`input, select, textarea` base — plus upstream's `select` arrow
replacement.

**A3. Icons.** Port upstream's Feather-style 16px stroked set, plus
`.btn--icon` and `.btn--danger`. Delete `⠿` `↑` `↓` `✕`. Delete becomes a
red-on-hover trash button exactly as `products/categorias.html` does it.

*One deliberate divergence:* upstream inlines every icon's path data in
its templates, which is why its `base.html` is 38 KB. This app needs more
icons in more places, so they go in `templates/_icons.html` as a Jinja
macro — `{{ icon('trash') }}`, not six lines of `<polyline>`. (D36.)

**A4. The rail.** Upstream's shape: `position:fixed`, `--rail-w`, brand
block with the logo, per-item icons, `<details class="rail__group">` for
Insumos/Mapeo, `.rail__foot` with a top border for Ajustes. The mapeo
badge stays — it is the only thing in the app that nags, and that is
correct.

**A5. Money gets tabular numerals.** `font-variant-numeric: tabular-nums`
on every money and cantidad cell. In a column of costs the operator
compares by eye, proportional digits make ₡1 046 and ₡818 misalign. Add a
`--num` token alongside `--ui` / `--mono`.

**A6. Touch minimums, everywhere in the shell.** 44px on any control a
finger can reach; `--tap: 44px` as a token so it is one value.

---

## 3. Phase B — the canvas

The creative half. Each proposal names what it is borrowed from and what
domain fact it serves.

### 3.1 The builder is three panes, not two

```
+-----------+---------------------------------+------------------+
| Paleta    | Pollo a la plancha              | Pechuga desh.    |
| [buscar]  | ============================    | ---------------- |
|           |                                 | ₡4.36 / g        |
| Insumos   | ## Pechuga desh.  180 g  ₡818   | mediana, 30 días |
|  Pechuga  | # Salsa criolla    45 g  ₡132   | 4 fuentes        |
|  Aceite   |   > Fondo oscuro                | 23 observaciones |
|  Sal      | # Aceite oliva     10 ml  ₡95   |                  |
|           | . Sal               2 g    ₡1   | [sparkline]      |
| Prepara-  |                                 |                  |
| ciones    | [ soltá acá ]                   | merma 12%        |
|  Salsa    |                                 | usado en 6       |
|  Marinada | ####  ##  #  .                  | recetas          |
|           | ₡1 046  ->  ₡1 088  (+42)       |                  |
+-----------+---------------------------------+------------------+
  palette          the plate                    the inspector
```

*Borrowed from:* an IDE (VS Code: explorer / editor / problems), Figma
(layers / canvas / properties).

The third pane is new and it is where **«¿Por qué este costo?»** finally
belongs. Today that explanation lives at `/insumos/{id}` — you have to
leave the recipe you are building to find out why a line is expensive, and
then find your way back. Selecting a line fills the inspector with its
`insumo_costs.Explicacion` (the panel already exists and already renders),
its sparkline, its merma, and its "usado en N recetas".

At 1440px the three columns are permanent — that is D40, chosen over three
alternatives, with the cost of the choice recorded there. **Below 1280px
the inspector becomes a drawer that slides in over the palette** (D41),
leaving the plate its full width; selecting a line opens it, dismissing it
returns the palette. The arithmetic settled this rather than taste: at
1024px the stage is 744px, so a third column would leave the plate 172px —
narrower than the quantity stepper it has to contain.

### 3.2 The line is a row, and it carries no chart of its own

*Borrowed from:* Linear's issue rows, Notion blocks.

Each recipe line stops being a `<tr>` in a `.grid` and becomes its own
element: drag handle, name, unit cost, quantity stepper, unit, line cost,
delete. **No per-row cost bar, and no `%` column.**

An earlier draft of this document specified both, *on top of* §3.3's
composition bar — three renderings of one fact, which is two too many. The
row wash was the loudest of the three and the least precise, and a column
of percentages beside a bar that already encodes them is a column nobody
reads. Cost share is §3.3's job and only §3.3's.

> **One signal per fact.** If a number is already drawn, do not also print
> it; if it is already printed, do not also draw it.

The one exception is a *scope the existing signal does not cover*, and
there is exactly one: the nested breakdown (§3.5) keeps its percentages,
because the composition bar spans the plate's own lines and not the
inside of a preparación's batch. In there the number is the only signal,
so in there it stays — and correspondingly the nested rows get no bars.

The quantity control becomes a **stepper** — `−` value `+` — with the
value still a real text input (comma accepted, per the CR decimal rule).
The steppers give the tablet a 44px target for the commonest edit in the
app, and the input keeps the keyboard path and the htmx debounce exactly
as they are.

### 3.3 One composition bar under the lines

A single horizontal stacked bar, full width, one segment per line, widths
= cost share, with a legend beneath. Hovering or tapping a segment
highlights its row; the reverse too.

**This bar is the only place cost share is expressed** (§3.2). It is
therefore not decoration and cannot be traded away for vertical space: if
it goes, the answer to "what is actually expensive here" goes with it, and
that is the question §10.1 says the screen exists for.

*Colour:* reuse `--chart-cat-1..12` from upstream's palette. Those twelve
hues were arrived at by an actual CVD-constrained search — the comment in
upstream's `app.css` records the deltaE figures — and inventing a second
categorical palette here would be both wasteful and worse.

*Implementation:* server-rendered inline SVG or a CSS flex row. No chart
library, matching the `cost_chart.py` precedent exactly: "a line and six
numbers do not justify 200 kB of vendored JavaScript."

### 3.4 The total is an instrument

Large, tabular, and it **shows its own delta**. After an htmx swap the new
total appears with a ghosted `+₡42` beside it that fades over ~1.2s. The
digits themselves snap.

A draft had them tick from the old value up to the new one. That fails §5's
own test: the quantity input is debounced at 600 ms, so typing a
three-digit quantity fires several swaps, and an animation the operator
sees forty times a day stops reading as feedback and starts reading as
lag. The chip carries the whole message on its own.

*Borrowed from:* a trading terminal's price tape; a supermarket
self-checkout, which is the closest everyday analogue to "a running total
that responds to what you put in".

*Implementation, and this is the part that keeps D16 intact:* the server
sends the previous total as a data attribute on the totals block. JS reads
two numbers it was handed and animates between them. **It never adds
anything up.** If the animation code were deleted, the correct total would
still be on screen.

### 3.5 The nest is drawn, not disclosed

*Borrowed from:* a file explorer's tree, and a flame graph.

A preparación line expands **in place** with indentation and a connecting
rule, not into a `<details>` in a table cell — and each child shows its
share of the batch as a **percentage**, per D39: the composition bar spans
the plate's own lines, so it says nothing about the inside of the salsa,
and the number is the only signal there. You can then read at a glance
that Salsa criolla is 10.8% of the plate while Fondo oscuro is 29.0% of
the salsa, which is the actual shape of where the money goes and is
currently two clicks and a mental division away.

Alongside it, a small always-visible **mapa de la receta**: "usada en 2
platos · contiene 3 preparaciones", each a link. `PLAN.md` §10 already
says the first question about a salsa is what breaks if you change it;
this is that question answered without asking it.

### 3.6 `/recetas` becomes a menu board

*Borrowed from:* an actual printed menu; Trello's columns; Linear's board
view.

Cards grouped by `categoria_menu`, each showing the plate name, **cost per
portion as the largest thing on the card**, the completo/incompleto pip,
and a sparkline of `receta_costo_historico` — which `cost_chart.py`
already renders and which is currently only visible one recipe at a time.
The menu-wide question ("which plates are drifting?") becomes a glance
instead of forty page loads, and it is exactly the surface Phase 2's
margin figures and Phase 3's quadrants will need.

**The sparkline is conditional, not standing furniture.** Today's real
history is 27 identical points per receta, because four insumos with
stable prices produce a flat line; nine cards each drawing a flat line is
nine pieces of ink saying nothing, and it teaches the operator to stop
looking. Draw it only when the variación clears a threshold (a few
percent over the window); otherwise the card shows the figure alone, and
the *presence* of a sparkline is itself the signal that something moved.

The table stays, behind a view toggle, for the operator who wants to sort
forty rows by cost. (Q9: which one is the default. It should be tried
against the real menu before deciding.)

`/preparaciones` keeps the table — a preparación is an ingredient, not a
menu item, and "usada en N" is a column, not a card.

### 3.7 The mapping queue becomes triage

*Borrowed from:* Superhuman / Gmail triage; a code-review queue.

**First, a bulk gate — and this is not optional.** A large share of the
1 420-product catalog will never be an insumo: the live data is full of
`CLORO ECONOMICO MFS 1/1 GL`, `CREMA LAVAPLATOS LIMON NIT 1/1KG`,
`VINAGRE BLANCO MFS 1/1 GAL`. Marching those through a one-card-at-a-time
queue is a punishment, and a queue design that ignores them is worse than
the list it replaces. The screen therefore opens on a **filter-and-reject
pass** — by category, by supplier, by "comprado en 180 días" — where a
whole filtered set can be dismissed as *no es un insumo* in one action.
The existing screen's «Solo comida» checkbox is the seed of this and must
not be lost in the redesign; an earlier draft of this document dropped it,
which was a regression.

**The «Solo comida» checkbox became «Solo lo que entra en una receta»
(2026-09-06).** The name was the honest part of a dishonest filter: it was
a *blocklist* of six non-food families, so every family upstream invented
later was admitted silently, and `empaques` was excluded outright. That
last part was simply wrong — a plate that leaves the kitchen in a
container pays for the container, so packaging has to be mappable like any
other cost. It is now an allowlist by prefix — `alimentos`, `bebidas`,
`empaques` — which admits new *leaves* of a family already chosen without
a code change, still requires a decision for a brand-new family, and
survives upstream renaming a family, which it has now done twice.

Then, one product at a time, full width, with the evidence laid out: the
supplier's description, the supplier, the purchase count, the parsed pack
size and **which rule read it** (`factor_origen` — the app already records
this and it is the reason a suggestion is trustworthy). One obvious
action, one obvious rejection, and `J` / `K` / `Enter` / `Esc`.

**Creating an insumo from the queue offers merma and método too
(2026-09-06).** The card wrote a real `insumo` row while offering only the
name, the unit and the factor, so every insumo born here started at 0%
merma on the global método — fine for salt, wrong for anything with a bone
in it, and fixable only by a second trip to the insumo card that nothing
prompted. It now offers the fields that decide a cost.

`ventana_dias` is deliberately left off: it only means anything once a
método is chosen, and a triage card is not where someone decides how many
days of history to average. It stays on the insumo's own form, beside the
método it qualifies.

**And this closed a hole rather than only adding fields.** `confirmar` was
building `Insumo(...)` by hand, which made the queue the one door into that
table that skipped every rule `insumos.validate` enforces — a merma of 995
or an invented método would have gone in unchallenged. Both doors now share
the rules, and a refusal re-renders the card with the message beside the
offending field and the typed values still in place, instead of a 500 that
costs the operator the group they had just finished ticking.

**The evidence row is pills, and a category reads as its name
(2026-09-06).** It showed `alimentos_verduras`. A slug is an identifier —
the operator never typed it, cannot act on it, and the underscore and the
family prefix are both noise at the point of decision. It now reads
«Verduras», in `restaurant-manager`'s own convention
(`products/_table.html`): a plain `.pip` for a real value, `.pip--faint`
for one that carries no information — «Otros», the catch-all upstream
assigns when nothing matched, and `Unid`, which is what 85% of the catalog
says in `unidad_medida`. Nothing on this row is a severity, so nothing on
it is amber or red.

**The names are mirrored, not derived.** `catalog_categoria` is filled from
`/api/v1/categorias` on every sync, because the name is not recoverable
from the slug: `alimentos_panaderia_postres` is "Panadería y postres", with
an accent and a conjunction no title-casing produces. An unknown slug falls
back to rendering the slug — the taxonomy is upstream's and it may add a
leaf between syncs, so the failure mode is the old behaviour rather than a
blank pill. The same helper fixed four other screens that were leaking
slugs: the insumo card, the insumo list, the insumo form's own dropdown,
and the builder's cost inspector.

**The card shows the cost it is about to create (2026-09-06).** It used to
show «última compra ÷ factor» — one observation, printed purely so a factor
wrong by a thousand was visible, and explicitly *not* a cost. It now runs
the real `costing.costo_unitario` over the real price points, so it can say
«costo» and mean it, and it still catches the factor error for the same
reason it always did: ₡3 802 por g for sugar reads as wrong instantly.

It recomputes on every change to factor, unidad, merma, método or ventana.
That is not polish — a card showing the median while the select says
«Máximo» is a wrong number on screen, which is the one thing this app
refuses to do. It reuses `costing.costo_unitario` rather than doing its own
arithmetic, so a preview that agreed with `/insumos/{id}` only by
coincidence is impossible; there is a test asserting the two match.

It is **this product's purchases only** — exactly right for a new insumo,
where this is the only fuente it will have, and deliberately partial when
mapping onto an existing one, where the real cost unions every confirmed
fuente.

**«Usar el de Ajustes» is gone from the método select, and this is a real
trade, not a tidy-up.** That option stored NULL, which means "follow the
global default as it changes" — genuinely different from picking Mediana,
but indistinguishable on screen while the global default *is* Mediana. The
card now writes an explicit método, so an insumo created here is pinned to
it and a later change to the global setting will not move it. Insumos
created from `/insumos/nuevo` still get the tracking option. If the global
default is ever changed, the insumos born in this queue are the ones that
will not follow.

A stack of 1 420 identical forms is a page that punishes you for having
work to do. A queue of one is a page that gets shorter.

**«Saltar»/«Volver» became a page strip (2026-09-06).** One product per
page, so a page number *is* a position in the queue — the thing the two
buttons could never say. Reaching row 300 took 300 clicks and still left
the operator with no idea where they were or how much was left. The strip
is `restaurant-manager`'s, ported: `services/pagination.py`'s
`pagination_window` and the `.pagenav` block, unchanged, because two apps
whose page strips disagree about when an ellipsis appears read as two
different products. `J`/`K` still work and now drive «Anterior»/«Siguiente»,
which are plain links — a page is a place, so it belongs in history and in
a bookmark.

**And confirming now keeps the operator's place.** It used to return to
`desde=0`, which was guarding against something real: a confirmation takes
the ticked look-alikes with it, and one of those can sit *behind* the
cursor, so a naively-kept offset would skip a product silently. Resetting
avoided the skip by throwing away the place. `posicion_tras_decidir`
subtracts only the decided rows that were actually behind the cursor —
computed before the write, while they are still pending — which keeps both.
The product just decided sat *at* the cursor, so the next one falls into
that exact slot and the offset should not move at all.

The rules do not move: `confirmar` is still only ever reached from a
person, still writes `mapped_by=human`, and `mapping_confidence` still
orders the queue and never auto-confirms (non-negotiable #4). This is a
presentation change and nothing else.

**What the gate needed that was not obvious** (step 2.3, 2026-09-05). The
dismissal has to be stored, and the tempting place —
`CatalogProduct.ignored` — is wrong twice over. It is *upstream's* flag:
`catalog_sync` overwrites it from the payload on every run, so a local
write there is silently reverted by the next sync; and writing it at all
breaks non-negotiable #3, which says the mirror is derived, disposable and
never hand-edited. So a local decision *about* a mirrored row lives outside
the mirror, in `catalog_descarte`, exactly as `InsumoMapeoRechazo` already
does for "this product is not that insumo" (D47). Under D15's no-Alembic
rule a **new table is free** — `create_all` creates it — while a new column
on an existing one is a hand-written `ALTER TABLE`, so this is also the
cheaper half of the trade.

Two properties of the gate are load-bearing:

- **The count and the action must see the same set.** The button names its
  own size («descartar 403») because a bulk dismissal whose scale you learn
  afterwards is not an action anyone can consent to. That makes a
  divergence between what was counted and what was dismissed the worst bug
  available on this screen, so the queue, the count, the badge and the
  dismissal all go through one `_filtros`.
- **It undoes as one action.** Four hundred rows dismissed by one click
  have to come back by one click, which is what `lote` is for.

**And a real bug turned up while wiring it, worth recording because it will
recur.** An unchecked checkbox renders its hidden mirror as `value=""`;
**FastAPI reads an empty *form* value as absent and substitutes the
default** (query parameters do not behave this way, which is what makes it
hard to spot). With a default of `"1"`, the gate counted the unfiltered set
and dismissed only the food subset — the operator approves one number and
gets another, silently. Fixed with two locks: the template writes `"1"`/
`"0"` explicitly rather than echoing the raw string, and the POST defaults
are `"0"`, the safe reading. There is a test named for it.

**Three corrections to the card, 2026-09-06.** None changes a rule; all
three are about the card reading as one thing.

- **The product's description is set in the UI face, not in `--mono`.**
  Monospace was saying "this string is upstream's, not ours" — true, and
  not worth a second typeface on the heading of a card that gets read four
  hundred times. It was the only monospaced heading in either app, and
  provenance is already carried in words by the pills under it (proveedor,
  categoría, unidad en factura, fuente). The look-alike rows
  (`.parecido__d`) follow, for the same reason and because they are the
  same string.
- **The cost panel is «Costo», not «Costo que quedaría».** The conditional
  was hedging about something the card already makes obvious: nothing on
  this screen is written until «Confirmar». A heading that re-states the
  screen's mode is a heading doing no work.
- **«Confirmar» moved to the right, clear of the cost panel.** It sat flush
  under the panel's lower edge and left-aligned, which read as one more of
  the panel's own controls rather than as the one thing on the card that
  writes. It is `.actions--fin` — `flex-direction: row-reverse`, *not*
  reordered markup, because implicit submission (Enter inside a text field)
  picks the first submit button in DOM order and that must stay
  «Confirmar», never «No es ese».

**The look-alikes became a grid, and the factor box got a name
(2026-09-06).** Three problems, one of them a bug that had been on screen
since the block shipped.

- **Every row was rendering as four stacked lines.** The list sat inside a
  `<div class="field">`, and `.field label{display:block}` is (0,1,1) —
  it beats `.parecido{display:flex}` (0,1,0), so the checkbox, the
  description, the meta and the factor each took their own line and four
  look-alikes filled the viewport. This is the *same specificity trap*
  already documented above `.check` in `app.css`, hit a second time, and it
  is worth stating as a rule: **`.field` is for one labelled control, not
  as a container for anything that needs a heading.** A list of rows gets
  its own block and its own `.parecidos__t` heading.
- **The rows now flow, as many per line as the width honestly fits** —
  `grid-template-columns: repeat(auto-fit, minmax(360px, 1fr))`, so three
  columns on the desk, two on a laptop, one on a phone, with no hardcoded
  N and no breakpoint. `auto-fit` and not `auto-fill`: with two look-alikes
  auto-fill keeps the empty third track and leaves a hole beside them,
  which also wraps the meta line for no reason. A group of five went from
  five full-width rows — enough to push «Confirmar» below the fold — to two
  lines of about 54px.
- **The factor box says what it is.** «5000» in an unlabelled input beside
  a product name is read as a price at least as readily as a factor, and
  the `placeholder="factor"` that used to carry the word disappears at
  exactly the moment there is a value worth questioning. It is a real
  `<label for>` now, using the same word as the field above the cost panel.
  Which is also why the row is no longer a `<label>` wrapping everything:
  that would make the «Factor» caption toggle the checkbox. The row is a
  div holding two labels — the text block points at the checkbox, the
  caption at that row's own factor.

**And «Sigue: <producto>» is gone from the card.** It named the next
product in the queue, which is a thing the operator finds out by pressing
«Siguiente» a moment later; on a screen whose entire argument is *one
product at a time*, a second product's name at the bottom is the one piece
of furniture working against it. The page strip already says where they
are. `siguientes` came out of the router's context with it.

**El cribador: the filters became settings, and gained a category
(2026-09-06).** The gate's own controls were the last part of this screen
still wearing the shape of a form.

- **The two flags were invisible.** They were `.check` labels, and `.check`
  hides the native box and draws its own `.check__box` — a span this markup
  never had. What reached the screen was three words of clickable text with
  no control anywhere near them: a switch that looks like prose reads as
  editable text, or as nothing. (Third sighting of that class's trap. See
  the look-alike note above, and `app.css`'s own comment over `.check`.)
  They are **chips** now — bordered, filled and ticked when on — which also
  states the right thing about what they are: a filter is a *state* the
  screen is in, not a field being filled. They moved onto their own line
  under the search box, which is what stops the labels running off the
  right edge of the card.
- **A category picker, and it takes several at once.** «Verduras y frutas»
  is one afternoon's pass through the queue, and a picker that takes one
  category at a time is how the second one never happens. Not a
  `<select multiple>`: that does take several, but only by ctrl-clicking,
  which nobody discovers and no tablet can do — and this queue is meant to
  be worked on a tablet. It is a `<details>` disclosure over a list of
  checkboxes, so it opens with JavaScript off; a small click-away script is
  the only enhancement, and it adds nothing the summary click cannot do.
- **Every option carries its count, and the counts ignore the category
  filter itself.** «Verduras 40» is what says where an afternoon goes, and
  a category with nothing left is simply not offered. `facetas_categoria`
  applies the other three filters and never its own — a facet that empties
  the list it was ticked from can never have a second category added to it.
- **What is picked is visible without opening the panel.** A count on a
  closed disclosure says how many and not *which*, and «which» is what
  decides whether the number under it is the one the operator meant. Each
  picked category is a chip that drops itself.

**And the gate's load-bearing property now has a fourth filter to keep.**
The count and the dismissal must see the same set — the button names its
own size, so a picker the count honours and the POST forgets would dismiss
hundreds of rows nobody approved. Three locks, all tested: `_filtros` takes
`categorias` and every caller passes it, the template mirrors each ticked
slug into every POST form through one `filtros_ocultos()` macro, and the
page strip carries them on each link so page two cannot silently widen.

**Two smaller things fell out of it.**

- **`_volver` percent-encodes now.** A search for «arroz & frijol» used to
  end the query string at the ampersand and drop every filter after it.
- **The queue's `J`/`K`/`Enter` keys stop at the filter card.** `Enter` on
  «Aplicar» is a button press, and without the guard it submitted
  `#form-mapeo` — writing a mapping when the operator meant to apply a
  filter.

**The scope became a setting, and the categories became upstream's own
multiselect (2026-09-06).** Second pass over the same card, and the first
one had the components right but the *shapes* wrong.

- **«Solo lo que entra en una receta» and «comprado en 180 días» are not
  controls on this screen any more.** They are a decision about how this
  restaurant works — which families can be costed into a plate, how far
  back is worth deciding about — and re-taking that on every visit is not
  a thing anyone does. Worse, they sat one stray click away from the
  button that dismisses everything matching them. They live in
  `app_setting` now (`mapeo_solo_receta`, `mapeo_dias_reciente`), get a
  panel on `/ajustes`, and the queue reports them as two `.pip`s with
  «Cambiar en Ajustes» beside them. Renamed while they moved: the pills
  read «Alimentos, bebidas y empaques» and «Últimos 180 días», which is
  what the filters actually do rather than what a checkbox would have to
  promise.
- **`mapeo_dias_reciente` is a number, and 0 means no limit.** `180` was a
  literal in the service (`DIAS_RECIENTE`), which is the shape PLAN.md §15
  exists to prevent; it stays as the column default and nothing else.
- **The category picker is `.msel`, upstream's own multiselect — and it was
  already in this app's `app.css`, ported and unused.** Class for class,
  chips-inside-the-control and all: a checked box renders as a solid
  removable chip in the control and its row *disappears from the list*, so
  the list only ever offers what is not picked yet. The one addition is
  `.msel__n`, the pending count per category, which is the number that says
  where an afternoon goes — and it is why `.msel__opt` is `display:flex`
  here and `display:block` upstream. The chip reads `[data-msel-label]` so
  the count does not ride into it.
- **It sits beside the search box**, because those two are the pair that
  narrows *this visit*; the scope, which does not, is a line of text under
  them.
- **The «Descartar dice…» paragraph is gone.** The button says «Ninguno es
  insumo — descartar 431», which is the whole sentence. The undo button
  that lived in that paragraph stays.

**Two things worth knowing about what this removed.**

- **`_bandera` and its two tests are gone, and that is the point.** The
  flags rode in the request as checkboxes; an unchecked box posts
  `value=""`, FastAPI reads an empty *form* value as absent and substitutes
  the default, and the gate could therefore count 2 and dismiss 1. Two
  locks held that door shut. The scope never enters a request now, so the
  door is not there. `test_una_bandera_vacia_no_invierte_el_filtro` is
  replaced by `test_el_porton_actua_sobre_el_alcance_guardado`, which
  asserts the property the old test was really about — the set dismissed is
  the set counted — across a settings change, the only way the scope moves.
- **`app_setting` finally has a reader.** It has claimed to be "seeded on
  first boot" since it was written and nothing ever seeded it; the table
  was empty on the live database. `_fila_ajustes` creates the row on first
  read, so a fresh install and a two-year-old one answer the same thing.
  Adding the two columns needed a hand-written `ALTER TABLE` — D15, no
  Alembic — and it is recorded in CLAUDE.md's known-debt list, which until
  now said «none so far».

**The card came off, the gate came off, and filtering went live
(2026-09-07).** Third pass, and this one is mostly subtraction.

- **«Qué se está revisando» is not a card.** A card earns its heading, its
  border and its icon by grouping things that need to be named together.
  This one was wrapped around a search box, on the screen the operator
  opens four hundred times, and the heading told them what they could see.
  What is left is the bar and one line of pills saying what the queue is
  scoped to.
- **Typing is the filter.** «Filtrar» was a second step for a decision
  already made by typing, and it was made a lot. The form is `hx-get`
  against its own route with `hx-select="#cola"`, so a live filter renders
  through *the same route and the same template* a full page load does —
  there is no second query, no fragment template, and no way for the two to
  compute different queues (D16's rule, the same one `_cuerpo.html`
  follows). `hx-push-url` keeps the result bookmarkable, and the search
  input carries an id so htmx restores the caret after each swap.

  **The 500ms debounce is a cost setting, not a latency one.** Every render
  of this screen computes one suggestion, which with a key configured is one
  billed call to Claude — so a request per keystroke would be a bill per
  keystroke. `delay:` restarts on each event, so a typed word costs one call
  (verified in a browser: seven characters, one request). The number is set
  for the hunt-and-peck typist, who at 300ms was paying per character.

  The button is still in the markup and still works; `.cribar__ir` is
  `display:none` except under `.sin-js`, the same trick the simulador's
  «Recalcular» uses. Ticking a category also filters live, and because only
  `#cola` is swapped the panel stays open while several are ticked — which
  is what the «Aplicar» button used to be for.
- **«Ninguno es insumo — descartar N» is gone, and so is the count beside
  it.** The gate existed because a 1 420-product catalog is full of cloro,
  servilletas and detergente, and marching those through a one-at-a-time
  queue is a punishment. **The scope setting does that job now** — it keeps
  `limpieza`, `menaje`, `higiene` and `oficina` out of the queue by family,
  server-side, without anyone pressing anything. What the gate would still
  have been for is "these 430 alimentos, none of which I use", which is a
  much weaker case for a four-hundred-row irreversible-feeling button
  sitting next to a search box.

  **The route, the service and their tests stay.** Rows dismissed under the
  old button are still dismissed, `restaurar` is still the way back, and
  «Traer de vuelta los N descartados» still renders when there is something
  to bring back. Deleting a tested bulk operation is a different decision
  from taking its button off a screen, and only the second one was made
  here. `descartar`'s docstring says so at the top, in capitals, along with
  the rule that has to hold if it ever gets a button again.

**And one bug the live filter introduced, caught before it shipped.** The
queue's `J`/`K`/`Enter` script lived inside what is now `#cola`. htmx
executes `<script>` in swapped content, so every keystroke of a live search
would have bound another `keydown` listener — after ten characters, `J`
would page ten products forward. It moved out below `#cola`; it looks
everything up by id at event time, so it never needed to be in there. There
is a browser check for it: type, clear, type again, then press `J` once and
land exactly one product on.

### 3.8 Búsqueda global (and only incidentally a command palette)

`Ctrl-K` / `⌘K`. Type a name, land on the receta, preparación or insumo.

**Scoped down from an earlier draft, deliberately.** That draft pitched a
full command palette — actions, "agregar «pechuga»" from inside the
builder, the Raycast idiom — on the grounds that it is the single feature
that most changes an app's *feel*. For one operator, six screens and forty
plates, that is instrument cosplay: the nav already reaches any screen in
two clicks, so a palette of *commands* would be answering a question
nobody has.

What the app genuinely lacks is **search at all**. There is no way to
find an insumo except by paging `/insumos`, and that gets worse with every
mapping confirmed. So this is a search box that happens to be summoned by
a keystroke — which is worth building, and is a much smaller thing.

*Borrowed from:* VS Code and Linear's quick-open, minus the command half.

Cheap, too: one route returning an HTML fragment, one `<dialog>`, and
htmx's existing `hx-get` on keyup. Upstream's `.sheet` (a native
`<dialog>` with a backdrop) is already the modal component to use.

**Built 2026-09-06, and the estimate held** — one route, one `<dialog>`,
one `hx-get` on keyup. Three things worth writing down.

**The keystroke is not the feature; the route is.** A `<dialog>` cannot be
opened without script, so a `Ctrl-K`-only search would be a feature the
no-JavaScript path simply does not have — the same hole D34 found in
drag. `/buscar` is therefore a real page with a real form, the rail
carries a real link to it, and the sheet is a faster way to reach the same
place. That is D35's promise applied to the one screen §6 had not
anticipated.

**The result order is fixed, not scored.** Platos, then preparaciones,
then insumos. A relevance blend would reorder itself between keystrokes,
and a list you are about to press Enter on is the one place that must not
happen. The operator searching «pechuga» is also far more often heading
for the plate than for the ingredient.

**Archived rows appear, and say so.** "Where did that plate go?" is
exactly what search gets asked, and answering «nada se llama así» when it
is merely archived sends the operator looking in the wrong place.

*One thing to watch for:* the sheet lives in `base.html`, so it is on every
page — including `/buscar`, where it would render the results fragment a
second time and put `id="buscar-hits"` and `id="hit-primero"` on the page
twice. It is suppressed there, and there is a test that counts them.

### 3.9 Modo simulador — probar sin guardar (D42)

**In scope.** A mode you turn on from the builder: quantities move, the
cost recomputes live, and nothing is written until you say so. It ends in
«Guardar» or «Descartar». The toggle reads **«Modo simulador»** —
«modo tanteo» was the working name and it failed the only test a label
has, which is that the person reading it should not have to ask.

The case it serves, with the live plate: the pechuga is 64% of Pollo a la
plancha, so "what if I go from 180 g to 160 g?" is the first question
anyone asks. At ₡4.36/g the line goes ₡784.80 → ₡697.60 and the plate goes
₡1 225.75 → ₡1 138.55. Today the only way to ask is to make the edit for
real, read it, and edit it back — two writes to answer a question.

**Four rules, and the last two are the ones that bite:**

- **The math never moves.** The simulador posts to a costing endpoint that
  returns a figure without writing. There is still no arithmetic in the
  browser (§6), and no second cost engine.
- **Scratch state lives in the session, keyed by receta** — not as a
  `borrador` row (this answers Q11). A row survives a reload, which sounds
  better until you price it: with no Alembic (D15) it is a hand-written
  `ALTER TABLE`, and it puts a not-real cost into a table the rest of the
  app reads. The session's failure mode is that a simulation is lost and
  the plate is unchanged, which is the safe direction to fail in. Leaving
  the page discards, and the banner says so before you go.
- **A simulated cost must never reach `receta_costo_historico`.** That
  table rides the catalog sync and is the app's record of what a plate
  actually cost on a given day. A snapshot that picked up somebody's
  half-finished "what if" would be a wrong number that looks exactly like
  a right one — the precise failure this whole design exists to prevent.
  The snapshot reads committed rows and is blind to session state, and
  there should be a test that says so.
- **Simulating a *preparación* has to say whose cost it is showing.**
  Changing the salsa's yield or one of its lines changes every plato that
  uses it — the live data already has one salsa feeding two platos, and Q4
  says three levels is the real depth. So while the mode is on inside a
  preparación, the screen also lists the affected recipes with their
  would-be new costs. "This salsa gets 8% cheaper" is not the decision;
  "these four plates get cheaper by these amounts" is. If that proves too
  much for one screen, the fallback is to refuse simulation on a
  preparación in use and say why — never a preview that silently means
  something narrower than the operator will read into it.

**Phase 2 inherits this rather than inventing a second one.** *Precio
sugerido* — move a target food cost, watch the price follow — is the same
affordance pointed at a different input. Building the modo simulador here
means Phase 2 extends a control the operator already knows, and it is the
reason this is worth building before the margin work rather than alongside
it.

Note the scope this adds: every other item in §3 is a presentation change
over figures the server already computes. **This is the one item that
needs a new route** (a costing endpoint that returns without writing) and
new session state. That is the price of it, taken deliberately.

**What it cost when it was built** (step 2.5, 2026-09-06), and one thing
that turned out cheaper than the plan assumed.

**The scratch state is the form, not a session — Q11 is revised** (D48).
Q11 was posed as "session or a `borrador` row" and the row was rightly
rejected: a hand-written `ALTER TABLE` under D15, and a not-real cost in a
table the rest of the app reads. But the session half is not needed
either. The simulador is one screen whose inputs *are* the scratch state:
each recompute posts them and gets a rendered answer, «Guardar» posts the
same values to the ordinary validated write path, and «Descartar» is a
link.

That satisfies every constraint D42 lists and removes a whole class of
state — no cookie, no server-side dict, no eviction, no restart
sensitivity, and no dependency (Starlette's session middleware is not
installed here, and this does not need it). It also matches D42's own
semantics *better*: "leaving the page discards" is what a form does by
construction, whereas a session would have helpfully restored a
simulation the operator had already walked away from.

**And the promised "new route" is a route but not a new engine.**
`receta_costs.costear` already accepted an injected `grafo`, so a "what
if" is that graph with different numbers in it — built with
`dataclasses.replace` on frozen dataclasses, costed by exactly the same
`graph.cost_recipe`. There is no branch in the arithmetic, no parallel
implementation, and nothing in the browser. §6 said "the moment a reviewer
sees a number being computed in `static/vendor/`, that is the bug"; there
was never an opportunity for one.

That is also what makes D42's third constraint structural rather than
careful. **A simulated cost cannot reach `receta_costo_historico`** — not
because the snapshot is written to avoid it, but because the substitution
happens on in-memory dataclasses between `cargar_grafo` and
`cost_recipe`, so no row is touched and no transaction is opened. The
snapshot reads committed rows. There is a test that simulates 999 g, runs
`instantanea`, and asserts the stored figure is the 180 g one.

### 3.10 A first run that has a shape

The empty-state prose in `list.html` is genuinely good — "un insumo es lo
que usa la cocina: «Pechuga deshuesada», no «filete de pechuga deshuesada
de Aviocal»" — and it is stranded at the bottom of an empty card.

Give `/recetas` a real first run: **sincronizar → mapear → crear un insumo
→ armar la primera receta**, as four steps whose completion the app can
evaluate from data it already has (`sync_run` rows, pending mapeo count,
insumo count, receta count). It is the only screen that ever shows it, and
it disappears for good once step four is done.


**Built 2026-09-06, and the constraint that shaped it** (D50): every step
is evaluated from rows the app already has — a successful `catalog_sync_run`,
a `confirmed` `InsumoFuente`, an `Insumo`, a `Receta`. Nothing is stored.

That is not a convenience, it is the whole design. A checklist with its
own progress column would be a *second source of truth* about whether the
catalog has been synced, one that can disagree with `catalog_sync_run` and
that somebody would eventually have to reconcile. Storing it would also
mean a table to migrate under D15, for information the app can already
answer.

Two details the rows settle that a stored flag would have got wrong:

- **A failed sync does not count as synced.** It left the mirror exactly as
  it was, so ticking that step would be a claim the next step immediately
  contradicts.
- **A suggestion does not advance the mapping step.** Nothing is active
  until a person confirms it (non-negotiable #4), so a queue full of
  `suggested` rows has advanced nothing — the step reads `status =
  'confirmed'` and only that.

It shows on `/recetas` and nowhere else, because that is where `/` lands;
a checklist repeated on six screens is nagging. And it is gone for good
once the fourth step is done — not collapsed, not dismissible. A card that
survives its own purpose is furniture.
### 3.11 Vista de cocina — the recipe at arm's length (D43)

*Borrowed from:* a printed spec sheet on a pass rail; a kitchen display
system.

A **read-only** mode of a receta, at its own route (`/recetas/{id}/cocina`),
built for a tablet propped up in the kitchen rather than a laptop on the
office desk:

- **Quantities and names, at reading-across-the-counter size.** The
  quantity is the largest thing on each line and sits in its own
  right-aligned column, so the list scans as a column of numbers. Body
  type steps up; the 44px minimum stops being a floor and becomes small.
- **The notas de producción are a first-class block**, not a card at the
  bottom of a costing screen. In this mode they are half the point.
- **No costs, and no controls.** Not an oversight — a cook does not need
  the plate's food cost, and a tablet on a pass is the least private
  screen in the building. It also removes every accidental-edit risk from
  the one surface where hands are wet and busy.
- **Preparaciones expand inline** to their own ingredient list, because
  "what goes in the salsa" is exactly the question asked at the stove.

The design already exists: this is the left column of the discarded
**Opción B**, which separated what a dish *is* from what it *costs*. That
option was set aside as a builder layout, but its recipe pane was the
strongest thing in it, and it lands here intact rather than being thrown
away.

Type scale is the thing to get right, and the thing that is expensive to
retrofit — which is why this is decided now rather than after the shell
pass locks a scale chosen for editing.

### 3.12 Las formas de edición caben en una pantalla (D44)

*Borrowed from:* Linear's settings panes and Stripe's dashboard forms —
dense cards under an icon-headed title, with the explanation one click
away rather than one screen away.

`insumos/form.html` and `recetas/form.html` were the one part of the app
that §2's "the shell is upstream's, verbatim" left visibly wrong, and not
because anything was mis-ported. Upstream's forms are short — a proveedor
is a name, a cédula and a checkbox, and one 620px column is right for
that. **These two are half teaching prose.** The «Unidad base» hint alone
is five lines explaining that the field says how the cost is *stored* and
not what a recipe may write (D30) — prose that has to stay, because
misreading that field is a silent factor-of-1000. One field per row plus
that prose ran the insumo form ~1200px tall for seven inputs.

**The target is a screen, not a width.** A first pass moved the prose into
a sidebar column beside the fields, which fixed the width and left the
height alone. That was the wrong measurement. What hurts on these screens
is scrolling: *Guardar* and the field you are unsure about should be
visible at the same time, and on a form you scroll they never are. Both
forms now fit 1080p with nothing to scroll — the insumo form in about
520px, the receta form in about 640px.

Three things get it there.

**One card per dimension, and cards sit side by side in rows.** The form
stops being a column of fields and becomes rows of objects. The insumo
form is *Identidad · Unidad base · Merma* over *Método de costeo · Notas*;
the receta form is *Tipo de receta*, then *Identidad · Porciones* (or
*Rendimiento*), then *Descripción · Notas de producción*. Upstream's
`.row` already does the even split and already gives its cards equal
height, so this is its component used as intended, not a new one.

**And the test for "a dimension" is worth stating, because it is the part
that is easy to get wrong in both directions:**

> **A card is the group of fields that fail together** — one explanation,
> one class of mistake. If two fields need two different warnings, they
> are two cards.

*Unidad base* and *merma* looked like one card — a first draft headed them
«Unidad y merma» — and are not. `unidad_base` is **notation**: D30 says it
changes no value, only its denomination. `merma_pct` is **arithmetic**: it
multiplies the cost, ₡4 000/kg at 12% being ₡4 545/kg usable. They carry
two different warnings ("not how recipes write it" / "a property of the
insumo, not of each receta"), so they are two cards, and each summary now
carries its own instead of one fold holding both. The «y» in the heading
was the tell. The same test stops the split at *Identidad*: nombre and
categoría share one class of mistake and touch no arithmetic, so they stay
together — and it separates *Descripción* from *Notas de producción*,
because only the second is a kitchen document (§3.11 renders it as one).

**The prose folds — but its load-bearing sentence does not.** Each card
ends in a `<details class="ayuda">`, and this is the rule that matters:

> **The `<summary>` is not a label for the explanation. It *is* the
> explanation's one sentence that would cost money if it were missed;
> only the elaboration goes inside the fold.**

So «Es solo cómo se guarda el costo, no cómo se escribe en las recetas»,
«Es una propiedad del insumo, no de cada receta» and «El rendimiento no es
lo mismo que la merma» are on screen at all times, collapsed or not, and
what folds is the worked example behind each. Folding the warning itself
to save 120px would trade a scroll for the exact error this app exists to
prevent. A `<details>`, so it costs no JavaScript and survives with
scripting off — the same disclosure the rail's own Insumos group uses.

Two smaller things fall out.

**A preparación carries a categoría too.** `categoria_menu` is one column,
`recetas.validate` already stored it for either tipo, and the form simply
never offered it to a preparación — so this is a template change and no
service change, verified by round-tripping one through the real app. A
preparación wants grouping on `/preparaciones` the same way a plato wants
it on the board, and the label drops «del menú» because a preparación is
not on the menu.

**`[hidden]{display:none!important}` is now a rule**, and it is not
defensive clutter. `hidden` is a UA-stylesheet declaration, so *any*
author rule setting `display` beats it — upstream's own
`.row>.card{display:flex}` is enough. Without the guard both the
porciones and the rendimiento card render, both post, and the service
clears whichever the tipo says is wrong, so nothing looks broken until a
plato is saved carrying a stale rendimiento. Upstream hit the identical
trap once already and patched it one selector at a time
(`.msel__opt[hidden]`); one rule closes it for every `display` added
later.

**No new component vocabulary.** `.card`, `.card__h`, `.field`, `.row`,
`.check` and `.btn` are upstream's; `.rejilla`, `.row--tres`,
`.bloque__h` and `.ayuda` only arrange them. That is the test D33 has to
keep passing: a fix to a button here still pastes into the other repo.

### 3.13 The pattern generalises, and should (D45)

§3.12 produced three parts that are not really about forms, and each one
answers a problem §0 named on a *different* screen:

- **The icon-headed card** (`.bloque__h` + `.bloque__ico` + `.card__h`) —
  a card that says what it is at a glance instead of making you read its
  contents to find out. Its one discipline: **never reuse a rail
  section's icon for a card that is not that section.** *Tipo de receta*
  is a book, not `layers`, because `layers` is the preparaciones rail item
  *and* one of the two options inside the card — the same glyph would have
  meant two different things 40px apart.
- **Cards as the unit of grouping**, sized by the fail-together test, laid
  out in `.row`s.
- **The folded explanation with an unfoldable summary** (`.ayuda`).

The third is the valuable one, and it resolves a tension §0.2 and §3 never
named. This app has to *explain itself* — "every figure on screen can
explain itself" is Phase 1's claim, and the reason the operator trusts a
number they did not compute. But explanation is expensive in vertical
space, and §0.2's complaint is that the important things are buried.
Those pull opposite ways, and the app has so far resolved them per screen,
differently each time: `title=` tooltips (unreachable by tap, §4.2), a
`<details>` in a table cell (§0.2 calls this out as the wrong shape for
the recipe nest), five-line `.field__hint` paragraphs, and «¿Por qué este
costo?», which is a whole screen region.

**The proposal: one disclosure pattern, one rule, everywhere.** The
sentence that changes what the operator does is never inside a fold; the
worked example, the derivation and the caveat always are. Applied:

- **`/insumos/{id}`, the «¿Por qué este costo?» panel.** Today it renders
  the whole `Explicacion` — sources, the observation table, the exclusions
  — always open. The exclusions are the load-bearing part: "this cost is
  low because half its sources were skipped" is a summary sentence. The
  23-row observation table is elaboration and should fold. Biggest single
  win, and it costs only markup.
- **The mapping triage (§3.7).** The evidence — description, supplier,
  purchase count, parsed pack size, `factor_origen` — is exactly a summary
  plus a fold: *which rule read it* is the sentence, the parse's workings
  are the fold. A queue of one that is also a wall of text is not a queue
  of one.
- **`/ajustes`.** Three panels that are already cards; they get icons, and
  their explanatory paragraphs get the same treatment.
- **The inspector (§3.1)** already has this shape and should adopt the
  same classes rather than keeping its own.

What this does **not** extend to is the builder's canvas. §3.2's rows,
§3.3's composition bar and §3.4's total are direct-manipulation surfaces,
and a fold on any of them would put a number behind a click — which is
§0.2's complaint, not its fix. The pattern is for cards that *explain*;
the canvas does not explain, it responds.

**And one rule about shadows falls out of the audit** (D46). Removing
`.form__pie`'s drop shadow — it was the only one in either app sitting on
something that does not float — leaves the app's use of shadow exactly
consistent, so it is worth stating rather than rediscovering:

> **A drop shadow means "this floats above the page", never "this is a
> card".** The tooltip bubble, the `.msel` dropdown panel, the `.sheet`
> dialog, the mobile `.stickyact` and the inspector drawer have one. No
> `.card` in either app does, and none should.

---

## 4. Touch and tablet

**4.1 Rewrite `dnd.js` on Pointer Events.** `pointerdown` / `pointermove`
/ `pointerup` with `setPointerCapture`, plus `touch-action: none` on
handles. One code path for mouse, pen and finger — not a touch branch
bolted onto the HTML5 path, which is how this usually goes wrong.

**The rule that makes `dnd.js` safe is preserved verbatim, and is the
reason the rewrite is tractable:** it moves nothing on its own; every drop
ends by submitting a form that already exists in the page and that a
button already submits. Drag stays the fast path, never the only path, and
never a second implementation with its own bugs.

Long-press (~350ms) to lift on touch, so a scroll gesture is not misread
as a drag. Auto-scroll when a lifted item nears the viewport edge — on a
tablet, the palette and the drop target are frequently not on screen
together.

**4.2 Every hover-only affordance gets a tap equivalent.** Port upstream's
`data-tooltip` bubble; retire `title=`. Audit `.pal:hover`,
`.row-link:hover`, `.linea.is-destino`.

**4.3 Breakpoints, three of them:**

- `≥1280px` — three panes.
- `900–1279px` — palette + plate; inspector becomes a drawer.
- `<900px` — one column, palette above the lines (you pick before you
  place — the current rule, and it is right); rail collapses to icons at
  1100px and to an off-canvas sheet below 820px.

**4.4 `dnd.js` still has no automated test, and the rewrite makes that
worse.** Testing it needs a DOM, a DOM needs npm, npm is what D2 rules out
— that trade stands. What does not stand is having no written record of
what "working" means. Add `tests/manual/dnd.md`: a checklist naming each
case (a palette drop submits that item's own form; a reorder rewrites
`#form-orden`'s id list and submits it; a drop outside the receta submits
nothing; a long press lifts, a short drag scrolls) to be run by hand on a
mouse and on a real tablet after any change to that file.

**4.5 What actually landed, and the three places it departs from the plan
above** (step 2.1, 2026-09-05). `tests/manual/dnd.md` exists and is §4.4's
checklist; the rest of §4 is built. Three things came out differently
enough to be worth writing down rather than leaving as a silent diff.

**A drag starts only from a grip, never from the whole row.** §4.1 did not
say this and the HTML5 version did the opposite — `.linea` and `.pal` were
both `draggable` in their entirety. Under Pointer Events that is not
available: the row is *also* the click target that opens the inspector, it
holds a text input, a select and three buttons, and `touch-action:none`
across a whole row is what would kill scrolling. So both rows now carry a
six-dot grip, and it is the one surface that owns the touch gesture. This
is a **better** affordance than what it replaces, not merely a workaround
— HTML5 drag gave a tablet no visible indication that a row could be
moved, because it could not be.

The cost, stated plainly: **a finger cannot scroll by starting on a grip.**
That is unavoidable — `touch-action:none` is what makes vertical
drag-to-reorder possible at all, and `pan-y` would hand every vertical
gesture back to the browser, which is the direction reordering happens in.
The mitigation is that the grip is narrow and everything around it
scrolls, and the long press (§4.1) means a flick from a grip does nothing
at all rather than something wrong.

**The rail below 820px stays upstream's horizontal strip.** §4.3 proposed
an off-canvas sheet. Upstream already collapses the rail to a strip across
the top there, that rule came over with the shell and works, and replacing
it with a bespoke panel would diverge from D33's shared furniture for no
gain. The 1100px icons-only collapse — the one §4.3 got right and the one
that matters, because it is the 1024–1279px tablet where the plate is
starved — is built.

**And the rewrite fixed a bug the old module had all along.** A
preparación's «de qué está hecho el lote» block is a DOM *sibling* of its
line, not a child, so reordering moved the row and left the breakdown
attached to whatever ended up above it. It was invisible in practice
because the server re-renders on drop, but it made every drag past a
preparación show a lie for the length of the gesture — on the exact
three-level nest (fondo oscuro → salsa criolla → plato) that Q4 says is
the real case. The pair moves together now, and `tests/manual/dnd.md`
names it as its own check.

---

## 5. Motion

Small, and rule-bound, because an app that animates everything reads as
slower than one that animates nothing.

- 120–180ms, `ease-out`, and **only on state that changed**.
- `@media (prefers-reduced-motion: reduce)` kills all of it. Non-optional.
- **Three uses, and no fourth without an argument:** the total's delta
  (3.4), a line entering or leaving the list, and the lift/settle of a
  drag.
- Nothing animates on page load. A screen that assembles itself in front
  of the operator every time they navigate is a screen that wastes their
  time forty times a day.

---

## 6. What this costs, and what it puts at risk

**D16 survives — say it out loud in every review.** Every figure above is
rendered by the server from `receta_costs.costear`. The composition bar's
widths, the row fills, the delta's two endpoints: all server-side. There
is still no JSON endpoint returning costs and no arithmetic in the
browser. The moment a reviewer sees a number being *computed* in
`static/vendor/`, that is the bug.

**The progressive-enhancement promise changes, and the new one must be
written down.** Today the claim is "the screen works with JavaScript off".
With a richer vendored layer, the honest claim is narrower:

> **Every mutation in this app is an HTML form POST to a route that
> answers both a fragment and a 303.** With JavaScript off you get the app
> as it is today: no drag, no palette, no live delta, no triage shortcuts
> — but every plate can still be built, edited and costed.

`_responder`'s dual-shape answer is what makes that true, and
`tests/test_routes_smoke.py` already tests both shapes. Keep that test and
extend it to every new route; it is the only thing standing between the
promise and a lie. (D35.)

**The rename pass is the risky commit, and not for the reason it looks.**
A mistyped CSS class does not raise, does not 500, and the smoke test
cannot see it — it just renders unstyled, possibly on a screen nobody
opens for a week. Mitigation: do it as one mechanical commit with no
intended visual change, so the diff is reviewable and screenshots are
comparable.

**`tests/test_routes_smoke.py` gains a URL per new screen.** `CLAUDE.md`
already says so; it will be tempting to skip during a redesign, and every
one of those omissions is a 500 on a daily page.

---

## 7. Sequencing

Each step is independently shippable and independently revertible.

| step | what | why here |
|---|---|---|
| **2.0** | The shell (§2) — tokens, class rename, `a{}`, focus, icons, rail, tabular nums, tap targets | Boring, unlocks everything, no design risk |
| **2.1** | `dnd.js` → Pointer Events, touch targets, breakpoints (§4) | Unblocks tablet; riskiest file, done while it is still ~150 lines |
| **2.2** | The builder (§3.1–3.5), inspector-as-drawer below 1280px | The reason for the redesign |
| **2.3** | Menu board, insumo card, mapping triage **with its bulk gate** (§3.6–3.7) | Each is one screen, each independent |
| **2.4** | Vista de cocina (§3.11) | Rides 2.1's touch work; its type scale must be set before the shell hardens |
| **2.5** | Modo simulador (§3.9) | The only step needing a new route and session state; wants 2.2's builder finished under it |
| **2.6** | Global search, first run (§3.8, §3.10) | Genuinely polish, and neither blocks anything |

**All six steps are built** (2026-09-06). 2.0, 2.1, 2.2 — including the
two things it left open, the delta chip and the inspector drawer — 2.3,
2.4, 2.5 and 2.6, plus the edit forms below, which were not a step.

What this document is now is a record, not a proposal, and the decisions
in §8 should move into `PLAN.md` §17 where the rest of them live.

**The edit forms (§3.12) landed out of band**, between 2.2 and 2.1, at the
operator's request. They belong to no step above because §2 assumed the
shell's forms were upstream's and therefore already right; they were not,
for a reason §2 did not anticipate (D44). Nothing depends on them, so the
sequence is otherwise unchanged.

**§3.13 is a proposal, not a step, and it is deliberately not scheduled on
its own.** Rolling the disclosure pattern across the app as one sweep
would be exactly the risky commit §6 warns about — a wide, mechanical,
untestable diff. It rides the steps that were already going to touch each
screen instead: the «¿Por qué este costo?» panel and `/ajustes` at **2.3**
alongside the insumo card, the evidence block at **2.3** with the triage,
the inspector at **2.2**'s remaining work. Nothing new is added to the
plan; the screens already in it just get built to one rule instead of
four.

**2.5 was the step to watch, and it came in smaller than billed** (built
2026-09-06). It adds a route and a rule (`receta_costo_historico` must
stay blind to it) whose violation would be invisible on screen — but not
the session state it was budgeted for (D48), and not a second costing
path: `costear` already took an injected graph, so the "what if" is that
graph with different numbers in it, costed by the same `cost_recipe`. Its
downstream consequence stands: Phase 2's *precio sugerido* is built on top
of it.

**Do 2.0 and 2.1 before Phase 2 (precio y margen).** Phase 2 adds a price
input, a margin figure and a food-cost percentage to exactly these
screens. Building those on the current CSS means building them twice, and
the second time is under the pressure of a feature that is already
"finished".

---

## 8. Decisions this would add to `PLAN.md` §17

- **D33 — Shell/canvas split.** The chrome, lists and forms are
  `restaurant-manager`'s design system verbatim, class names included. The
  builder, cost panel and mapping triage are this app's own. *Because* a
  suite that ships together must share furniture, and *because* imitating
  a ledger's table idiom on a composition surface is what produced the
  problem being fixed.
- **D34 — Pointer Events, not HTML5 drag.** One code path for mouse, pen
  and touch. HTML5 DnD does not fire on a touchscreen, so the current
  builder has no drag at all on a tablet.
- **D35 — The revised progressive-enhancement promise** (§6), and the
  smoke test that guards it.
- **D36 — Icons are a Jinja macro** (`templates/_icons.html`), diverging
  from upstream's inlined path data, because this app has more icons in
  more places.
- **D37 — Reuse `--chart-cat-1..12`** for the composition bar rather than
  inventing a categorical palette. Upstream's was CVD-searched; a second
  one would be worse and would need maintaining.
- **D38 — `/recetas` defaults to the board**, table behind a toggle
  (settled 2026-09-04). A menu has categories and dishes people picture,
  and the board is that object; the table stays one click away for the
  "rank every plate by cost" question, which the board is bad at.
- **D39 — One signal per fact** (§3.2). Cost share is the composition bar
  and nothing else; the per-row wash and the `%` column are both out. The
  single exception is a scope the bar does not span — inside a
  preparación's breakdown — where the number is the only signal.
- **D40 — The builder keeps the three-pane layout** (decided 2026-09-04,
  after mocking three alternatives). The objection on the record, and it
  is a real one: at 1440px the palette and inspector hold **540 of the
  stage's 1 160px — 47% — permanently, for tools used in bursts**, leaving
  the plate 588px. Three alternatives were drawn and set aside: a
  full-width list with the palette as an add-bar (loses drag as the
  primary gesture), a recipe/costs split (puts a cost far from its line),
  and compact rows that expand on focus (adds a click to the app's most
  frequent action). The three-pane layout was preferred as drawn. **If the
  588px plate proves cramped against a real 12-line recipe, revisit this
  before adding anything else to the row** — the row is the constrained
  resource, and Phase 2 wants to put a price and a margin near it.
- **D41 — Below 1280px the inspector is a drawer, not a column** (settled
  2026-09-04). Decided by arithmetic, not preference: at 1024px the stage
  is 744px, and a third column would leave the plate 172px — narrower than
  the stepper it must hold. The palette keeps its column, because drag
  from the palette is the gesture a tablet most needs.
- **D42 — El «modo simulador» ships in this redesign** (settled
  2026-09-04, reversing a deferral taken the same day). Briefly it was
  moved to Phase 2 on the argument that *precio sugerido* is the same
  affordance and should be designed once. The reversal accepts that
  premise and draws the opposite conclusion: if it is the same affordance,
  build it **first**, against the simpler input, so Phase 2 extends a
  control the operator already knows instead of introducing one alongside
  a new pricing model. Named «Modo simulador», not «modo tanteo» — the
  working name needed explaining, which for a UI label is a failure. It
  carries three constraints: session-scoped scratch state (Q11), no
  simulated cost may ever reach `receta_costo_historico`, and simulating a
  preparación must name the recipes it would move. See §3.9.
- **D43 — A read-only *vista de cocina* ships** (settled 2026-09-04):
  `/recetas/{id}/cocina`, large type, quantities and notas, **no costs and
  no controls**. Decided now rather than later because its type scale is
  the expensive thing to retrofit once a scale chosen for editing is
  locked in. It reuses the recipe pane of the discarded Opción B.
- **D44 — The edit forms are rows of cards that fit one screen** (settled
  2026-09-05, revising a same-week first pass). A deliberate but narrow
  exception to §2's "the shell is upstream's, verbatim": the *layout*
  diverges, the class vocabulary does not. Upstream's forms are short — a
  proveedor is a name, a cédula and a checkbox — and 620px is right for
  them. These two are half teaching prose (D30's «Unidad base»
  explanation, merma vs rendimiento), which in one column ran the insumo
  form ~1200px tall for seven inputs. The measurement that matters is a
  screen, not a width: an earlier pass widened the form and left the
  height alone. **A card is the group of fields that fail together** — one
  explanation, one class of mistake — which is why *unidad base* and
  *merma* are two cards and not an «Unidad y merma» heading: one is
  notation, the other is arithmetic, and they carry different warnings.
  See §3.12.
- **D45 — One disclosure pattern for the whole app** (settled 2026-09-05).
  The `<summary>` is not a label for an explanation; it *is* the
  explanation's one sentence that would cost money if it were missed, and
  only the elaboration goes inside the fold. This is how the app carries
  the prose it genuinely needs — "every figure can explain itself" — without
  paying a screen for it, and it replaces four ad-hoc mechanisms
  (`title=`, a `<details>` in a table cell, long `.field__hint`
  paragraphs, a whole screen region). It extends to the «¿Por qué este
  costo?» panel, the mapping triage's evidence block, `/ajustes` and the
  inspector; it does **not** extend to the builder's canvas, where a fold
  would put a number behind a click. See §3.13.
- **D46 — A drop shadow means "this floats", never "this is a card"**
  (settled 2026-09-05, from an audit of every `box-shadow` in the file).
  The tooltip bubble, the `.msel` panel, the `.sheet` dialog, the mobile
  `.stickyact` and the inspector drawer float and have one. No `.card` in
  either app does. `.form__pie` had one and was the single exception,
  which read as a component borrowed from a different design system.
- **D47 — A dismissed catalog product is a local row, never a flag on the
  mirror** (settled 2026-09-05, with §3.7's bulk gate). `catalog_descarte`
  and not `CatalogProduct.ignored`: that column is upstream's own, rewritten
  by `catalog_sync` on every run, and hand-editing the mirror breaks
  non-negotiable #3. A local decision *about* a mirrored row lives outside
  the mirror, as `InsumoMapeoRechazo` already does. It also lands on the
  right side of D15: a new table is free under `create_all`, a new column on
  an existing table is a hand-written `ALTER TABLE`. The `lote` column makes
  a four-hundred-row dismissal undo as one action. See §3.7.
- **D48 — The simulador's scratch state is the form, revising Q11**
  (settled 2026-09-06, when §3.9 was built). Q11 chose "session" over a
  `borrador` row; the row's rejection stands, the session is unnecessary.
  The simulador is one screen whose inputs *are* the scratch state, so
  there is no cookie, no server-side dict, no eviction, no restart
  sensitivity and no new dependency — and "leaving the page discards" is
  what a form does by construction, where a session would have restored a
  simulation the operator had walked away from. See §3.9.
- **D49 — The kitchen view is a cost-free query, not a costed one with the
  money hidden** (settled 2026-09-06, with §3.11). `recetas.arbol_cocina`
  has no cost field at all, so D43's "no costs" cannot be broken by adding
  one `{{ }}` to a template on the least private screen in the building.
  It also makes the screen immune to pricing state: a receta whose
  ingredients have no prices, or whose graph has a problem, still says what
  goes in it — which is all a cook wanted from it. It expands one level by
  default, because §3.11's question is "what goes in the salsa", and the
  real three-level nest fully expanded is a wall of text on a display
  someone is reading from two feet away.
- **D50 — The first-run checklist stores nothing** (settled 2026-09-06,
  with §3.10). Every step is derived from rows the app already reads: a
  successful `catalog_sync_run`, a `confirmed` `InsumoFuente`, an `Insumo`,
  a `Receta`. A stored progress column would be a second source of truth
  about whether the catalog has been synced — one that can disagree with
  `catalog_sync_run`, and a table to migrate under D15 for information the
  app can already answer. It also gets two cases right that a flag would
  have got wrong: a *failed* sync does not count as synced, and a
  *suggested* mapping advances nothing (non-negotiable #4). See §3.10.
- **D51 — Search is a route that a keystroke reaches, not a keystroke**
  (settled 2026-09-06, with §3.8). `/buscar` is a real page with a real
  form and the rail carries a real link; the `Ctrl-K` sheet is a faster
  way to the same place. A `<dialog>` cannot be opened without script, so
  a keystroke-only search would have been a feature the no-JavaScript path
  does not have — the same hole D34 found in HTML5 drag. Results are
  ordered platos → preparaciones → insumos and never by score: a relevance
  blend reorders itself between keystrokes, and a list you are about to
  press Enter on must not. See §3.8.

## 9. The questions this opened, and how they were settled

All four were answered on **2026-09-04**. Nothing in this document is
waiting on a decision.

- **Q9 — Board or table as the default `/recetas`?** → **Board** (D38).
  Answered ahead of the real forty-plate menu rather than after it, so the
  caveat stands: the board has never been seen with forty cards, and the
  table toggle is the escape hatch if it does not hold up.
- **Q10 — Inspector column or drawer between 900 and 1279px?** →
  **Drawer** (D41), over the palette, with the plate keeping its width.
- **Q11 — Scratch state: session or a `borrador` row?** → **Neither: the
  form** (D48, revising the 2026-09-04 answer of "session" when §3.9 was
  actually built). The `borrador` row's rejection stands verbatim; the
  session turned out to be state nobody needed.
  *The original answer, kept because the reasoning against the row is
  still why:* The feature was briefly deferred, which would
  have deferred this question with it; it ships instead, so the question
  is answered. A `borrador` row costs a hand-written `ALTER TABLE` under
  D15's no-Alembic rule and puts a not-real cost in a table the rest of
  the app reads. The session's failure mode — the simulation is lost, the
  plate is unchanged — is the safe direction.
- **Q12 — A read-only kitchen view?** → **Yes** (D43), and early, because
  its type scale is what would be expensive to retrofit.

### What is still genuinely unknown

Not questions to answer at a desk — things only the real data can settle,
and worth writing down so they are not mistaken for oversights:

- **Whether the board survives forty cards.** Nine fit comfortably. Forty,
  across three or four categorías, may want denser cards or collapsible
  sections.
- **Whether the mapping triage is tolerable at 1 415 items** even with
  §3.7's bulk gate in front of it. The gate is a prediction; the honest
  test is one real session with the live catalog.
- **Whether the 588px plate (D40) is enough** once a twelve-line recipe
  exists, and once Phase 2 wants a price and a margin on the same row.
