# Menu Builder — Design Plan

**Status:** Phase 1, step 1.5. Steps 1.0-1.4 are built and verified
against live data (see §11); `receta`/`receta_linea` and the builder screen
are next. Phases 2 and 3 stay outlined deliberately short — they are real
commitments about *direction*, not finished designs.

**Companion app:** `restaurant-manager`, the same operator's
e-invoice-driven management suite for the same single restaurant in Costa
Rica. Menu Builder does not duplicate it; it picks up scope that app
deliberately deferred. Read §0 before anything else.

---

## 0. Why this app exists, and why it is a separate app

`restaurant-manager` ingests supplier e-invoices (Hacienda XML) from Gmail
and derives purchase history, price history, accounts payable, services,
fixed costs, petty cash, inventory and a spend dashboard from them. Its own
PLAN.md §1 lists, as **explicit non-goals for v1**:

> true consumption-based food cost, **recipe costing**, payroll, emitting
> sales invoices, multi-company.

That line is the seed of this app. Two more facts from upstream make the
shape of this one almost predetermined:

1. **Its product catalog is supplier-scoped, and always will be.** A
   `product` row is created per supplier from invoice lines. `models.py`'s
   own docstring: *"There is deliberately no cross-supplier canonical
   `product` layer yet (§6.5's human-confirmed mapping)."* So *filete de
   pechuga deshuesada* from Aviocal Altavista and *pechuga deshuesada* from
   Inversiones Hevar are, and remain, two rows. That is correct for
   purchasing — those are two different things you buy, at two prices, from
   two suppliers, and collapsing them would destroy the price history that
   makes upstream's price alerts work.
2. **Its PLAN.md §5.3 already designed the missing layer and never built
   it** — a canonical `product` (`canonical_name`, `category_id`,
   `canonical_unit`, `pack_size`) with `supplier_item` mapping rows
   carrying `mapping_confidence` and `mapped_by` (`auto`|`human`), governed
   by **D4: "Product mapping is human-confirmed — no stable cross-supplier
   identifier exists."**

**This app is that layer, plus what it was for.** The canonical layer is
called **insumo** here, because that is what it is for — the thing a recipe
consumes, not the thing an invoice sells. And the reason it is a separate
app rather than a module upstream is that its consumers are different
people asking different questions: upstream answers *"where is the money
going and what do I owe"*; this answers *"what does this plate cost me, and
what happens to it when chicken goes up."* Those two share exactly one
thing — the price series — and that is a contract, not a coupling.

**What we are not doing:** re-deriving upstream's numbers. Every price
figure in this app traces to a `precio_efectivo` upstream computed, over an
invoice line upstream ingested. If a figure here disagrees with the same
figure there, this app is wrong by definition.

---

## 1. Objectives

| # | Objective | Success criterion |
|---|---|---|
| O1 | Turn the supplier-scoped catalog into a canonical insumo list | Every insumo used in a live recipe has ≥1 confirmed source; the unmapped queue is drainable in one sitting |
| O2 | Know what a plate costs, today, without a spreadsheet | Any receta shows a costed breakdown in ≤2 clicks from the menu list |
| O3 | Make that cost honest about *which* price it used | Every cost figure can name its method, window, series and merma on screen |
| O4 | Support composed insumos and sub-recipes to arbitrary depth | A salsa used by six platos is edited once and all six re-cost |
| O5 | See a plate's cost move over time | Cost history chart per receta, same card shape as upstream's price histórico |
| O6 | Never present a confidently wrong cost | An insumo with no usable price makes its recipes `incompleto`, never cheap |
| O7 | Keep the operator's hands on identity decisions | No LLM-suggested mapping ever reaches a cost figure without human confirmation |

**Explicit non-goals for Phase 1:** menu pricing and margin (Phase 2),
menu engineering / sales mix (Phase 3), purchasing suggestions, production
planning, allergen and nutrition data, multi-site, multi-currency menus,
and any write path back into `restaurant-manager`.

**One non-goal that is permanent:** this app never writes to
`restaurant-manager`. Not a category, not a mapping, not a correction.
Upstream owns its own master data; this app is a reader. If a product is
miscategorized upstream, the fix happens upstream.

---

## 2. Design principles

1. **The upstream API is the source of truth for prices.** Everything this
   app stores about a supplier product is a *projection* — cached for
   speed, rebuildable by re-syncing, and never hand-edited.
2. **Identity is human-confirmed, suggestion is automated.** Inherited
   whole from upstream D4. The LLM proposes an insumo grouping; a person
   accepts it. The gap between those two states is a real, visible queue,
   not a confidence score quietly rounding to "probably fine".
3. **Every automatic decision records which rule fired.** Inherited from
   upstream. A `factor_conversion` of 5000 must be able to say whether it
   came from the description text, the XML `UnidadMedida`, a category
   default, or a person typing it.
4. **Absence of data is a signal, not a blank.** An insumo with no price
   observation in its window has *no cost*. Its recipes are `incompleto`
   and say which ingredient is missing. A zero would silently make the
   most profitable dish on the menu the one we know least about.
5. **Money is `NUMERIC`, never float.** Inherited. Every cost is CRC and
   net of IVA, and says so.
6. **Cost math is pure and tested.** Unit conversion, the costing methods,
   and graph traversal live in dependency-free modules with unit tests, the
   same way upstream keeps `currency.py`, `unit_conversion.py` and
   `period_keys.py` importable without a DB driver.
7. **The builder screen is the product.** Everything else is CRUD around
   it. Optimize the drag-and-drop, search-and-insert, type-a-quantity,
   watch-the-total-move loop before anything else gets polish.

---

## 3. Non-negotiables

Breaking one of these produces a plausible-looking cost that is wrong, which
is worse than an error.

1. **Money is `NUMERIC`, never float.** Every stored cost carries its
   currency (CRC) and its tax basis (net of IVA).
2. **Costs are net of IVA, on invoice date.** This is upstream D6's cost
   view, unchanged. Food cost is a margin question, not a cash question —
   do not "improve" it by using the gross figure the dashboard shows.
3. **The catalog mirror is derived and disposable.** `catalog_product` and
   `catalog_price_point` are rebuildable from the API at any time. Nothing
   in this app may hold data that exists *only* in the mirror.
4. **A product→insumo mapping is never active until a human confirms it.**
   `status` goes `suggested → confirmed`, and only `confirmed` rows feed a
   price series. LLM output lands as `suggested`, always.
5. **The recipe graph is acyclic.** Enforced on write (the edge is
   rejected, with the offending path shown), and defended again on read
   (traversal is depth-limited and cycle-aware) so a bad row from any
   source cannot hang a page.
6. **Unit conversion never crosses dimensions.** Grams do not become
   millilitres without an explicit per-insumo density. No implicit 1:1.
7. **An insumo with no usable price has no cost.** Never 0, never the last
   known price from outside the window without saying so.
8. **This app never writes to `restaurant-manager`.** Read-only, forever.

---

## 4. Architecture

```
                         Gmail (Hacienda XML)
                                 |
                                 v
                    +-------------------------+
                    |   restaurant-manager    |
                    |  product, product_      |
                    |  purchase, supplier     |
                    +-------------------------+
                                 |
                     GET /api/v1/...  (read-only, JSON)
                                 |
                                 v
     +--------------------------------------------------------+
     |                     menu-builder                        |
     |                                                         |
     |  catalog_sync ---> catalog_product, catalog_price_point  |
     |                          | (projection, rebuildable)     |
     |                          v                               |
     |  insumo <--- insumo_fuente (human-confirmed mapping)      |
     |    |             ^                                       |
     |    |             +--- llm_insumo (suggestions only)       |
     |    v                                                     |
     |  costing engine (pure) --> costo por unidad base        |
     |    |                                                     |
     |    v                                                     |
     |  receta / receta_linea (DAG) --> costo del plato          |
     +--------------------------------------------------------+
```

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 ·
Jinja2 + htmx · Docker Compose — deliberately identical to upstream, so
that one operator maintains one stack and conventions transfer verbatim.

**No build step.** Drag-and-drop is a vendored, dependency-free JS module
in `static/vendor/`, not an npm toolchain and not a CDN script — this app
runs on the restaurant's own machine and must work with the internet down.

**Ports and volumes**, chosen not to collide with upstream (which holds
`8000` and `5433`):

| | upstream | menu-builder |
|---|---|---|
| web | `127.0.0.1:8000` | `127.0.0.1:8001` |
| db | `127.0.0.1:5433` | `127.0.0.1:5434` |
| compose project | `restaurant-manager` | `menu-builder` |
| volume | `restaurant-manager_pgdata` | `menu-builder_pgdata` |

The Compose `name:` key is pinned for the same reason it is pinned
upstream, and that reason is documented in `docker-compose.yml` itself:
without it Compose derives the project name from the directory, and
renaming the checkout orphans the database in a way that reads exactly like
total data loss.

---

## 5. The upstream contract — `restaurant-manager` `/api/v1`

**This is step 0 of Phase 1 and it ships in the other repo**, before any
costing code is written here. It is deliberately small, additive, and
GET-only: one new `backend/app/routers/api.py`, two lines in `main.py`
(an import and an `include_router`), no schema change, no migration, no new
dependency, and no modification to any existing file beyond those two
lines. It also needs a decision-log entry and doc updates upstream — that
part is not optional, it is how that repo works.

**Why an API and not a direct read of upstream's Postgres.** Correct cost
depends on logic upstream already owns and already tests:
`services/currency.py`'s `effective_price`, `services/unit_conversion.py`'s
`infer_unit_ratios` (the CJ-case vs BOT-bottle normalization), the
parent/subsidiary family merge in `services/products.py` (Mayca, Gas
Tomza), and the `ignored` / credit-note exclusion semantics. Reading the
tables directly means reimplementing all of it here, and any drift between
the two copies surfaces as a silently wrong food cost — the exact failure
mode upstream's non-negotiables exist to prevent. The API lets this app
consume the *result* of that math instead of racing a second copy of it.

### 5.1 Endpoints

**The `v1` is load-bearing, not decoration** (D22). For a single local
install with one consumer on the same machine it would be theatre — you
would just change both repos in one sitting and never run two versions at
once. It is in the path because **this app is intended to ship to other
restaurants eventually** (2026-09-02), and the moment there are two
customers there are two independently-updated installs: customer A's
`restaurant-manager` will be older than customer B's `menu-builder`, and no
one can force a simultaneous upgrade across sites. That is exactly the
problem path versioning solves, and it is much cheaper to put the prefix in
now than to retrofit it across deployed installs later.

Because the prefix now means something, it carries an obligation: **a
breaking change to a payload means `/api/v2`, not an edit to `/api/v1`.**
Additive fields are fine and do not bump the version — the sync ignores
fields it does not know. `GET /api/v1/version` stays alongside it and does
the *other* job: it reports the minor contract revision so `catalog_sync`
can fail loudly on something it does not recognize, rather than silently
mis-parsing. Two mechanisms, two distinct jobs.

```
GET /api/v1/version
    -> {"app": "restaurant-manager", "api": "1.0", "schema_hint": "<date>"}

GET /api/v1/proveedores
    -> [{id, cedula, nombre, es_servicios, parent_supplier_id, activo}]

GET /api/v1/categorias
    -> [{id, slug, nombre, parent_id, kind, descripcion}]

GET /api/v1/productos?desde=<cursor>&limite=200&modificados_desde=<iso>
    -> {items: [Producto], next_cursor: <id|null>}

GET /api/v1/productos/{id}
    -> Producto

GET /api/v1/productos/{id}/compras?desde=<fecha>&hasta=<fecha>
    -> {items: [Compra], next_cursor: ...}
```

### 5.2 Payload shapes

**`Producto`** — one supplier-scoped catalog row:

| field | source upstream | note |
|---|---|---|
| `id` | `product.id` | |
| `internal_code` | `product.internal_code` | unique, stable — **this app's join key**, not `id` |
| `supplier_id`, `supplier_nombre` | `product.supplier` | |
| `family_supplier_id` | `Supplier.parent_supplier_id` or self | the merged identity upstream displays |
| `description` | `product.description` | first-seen `Detalle`, never overwritten |
| `cabys` | `product.cabys` | |
| `unidad_medida` | `product.unidad_medida` | XML `UnidadMedida` |
| `unidad_comercial_base` | derived | the *smallest* observed commercial unit — the unit `precio_efectivo_normalizado` is expressed in |
| `categoria_slug` | `product.category` | |
| `ignored` | `product.ignored` | this app must exclude these too |
| `last_purchase_at` | `product.last_purchase_at` | |
| `updated_at` | `product.updated_at` | drives `modificados_desde` |

**`Compra`** — one price observation:

| field | note |
|---|---|
| `fecha` | `FechaEmision`, the invoice date (upstream D21's basis) |
| `cantidad`, `unidad_comercial` | as invoiced |
| `moneda` | `CRC` \| `USD` |
| `precio_efectivo` | net of IVA, net of the line's discount, in `moneda`, in `unidad_comercial` |
| `precio_efectivo_crc` | the same figure **always expressed in CRC** — see the coalescing rule below; `null` only when a USD line had no rate |
| `unit_ratio` | how many base units one `unidad_comercial` is worth |
| `precio_efectivo_normalizado_crc` | `precio_efectivo_crc / unit_ratio` — **the figure this app costs from** |
| `iva_pct`, `descuento_pct` | for display and audit |
| `clave`, `numero_linea` | the natural key back to the invoice |

**Four properties this app depends on and upstream must not quietly
change:**

- **The endpoint must coalesce the CRC figure; it must not pass
  `ProductPurchase.precio_unitario_crc` through raw.** That column is
  `NULL` for *every CRC purchase by design* — upstream's own model
  docstring: *"None for a CRC purchase (no conversion needed)."* Verified
  against the live database on 2026-09-02: **17 272 of 17 645 rows are CRC
  with a `NULL` `precio_unitario_crc`**, 63 are USD (all of which do have a
  rate), and 310 have a `NULL` `moneda` entirely. An earlier draft of this
  plan said to drop observations with a `null` CRC price — that rule would
  have silently discarded **98% of the catalog** and left almost every
  insumo with no cost. The contract is therefore:

  ```
  precio_efectivo_crc = precio_efectivo_crc_column  if moneda = 'USD'
                      = precio_efectivo             if moneda in ('CRC', NULL)
  ```

  A `NULL` `moneda` is treated as CRC: all 310 such rows fall between
  2024-07-01 and 2025-08-12, and Hacienda's `CodigoMoneda` is optional with
  CRC as its default. `null` therefore survives in the payload for exactly
  one case — a USD line whose date had no exchange rate — and *those*
  observations are dropped from the series, with the drop counted and shown
  on the insumo's cost panel.
- `precio_efectivo_normalizado_crc` is expressed in `unidad_comercial_base`,
  *not* in whatever unit a given invoice happened to use. This is what makes
  a case purchase comparable to a bottle purchase, and it is why
  `factor_conversion` in this app is defined against the base unit (§7.2).
- Notas de crédito are already excluded — upstream's non-negotiable #5 means
  their lines never became `ProductPurchase` rows at all. This app must not
  try to net them again.
- The endpoint computes `unit_ratio` with upstream's existing
  **`services/products.infer_unit_ratios_for_products`** (Q1, answered):
  ratios are derived per request from a product's whole price history, not
  stored, and that batch helper already exists and already does the whole
  list in one query. Do not add a stored column for this and do not call the
  single-product version in a loop.

### 5.3 Sync into the local mirror

`services/catalog_sync.py` pulls with `modificados_desde` = the last
successful sync watermark, upserts `catalog_product` on `internal_code`,
and appends `catalog_price_point` rows keyed `(clave, numero_linea)` —
the same natural key upstream uses, so a re-sync is idempotent by
construction and a full rebuild is `DELETE` + re-pull.

Manual button in Ajustes plus a scheduled pull; the watermark, last run,
row counts and any error are visible on that page. **Sync failure never
blocks costing** — the mirror simply ages, and every cost figure carries
the mirror's own as-of date so a stale number is legible as stale rather
than as current.

### 5.4 The rest of the API surface — where else, and where deliberately not

The `/api/v1` above is the only surface Phase 1 needs. Three more are
worth building later, in this order, and one place must stay API-free.

**A. Upstream, `/api/v1/inventarios` (Phase 2–3).** Upstream's Inventarios
module already derives *entradas por compra* automatically and records
physical counts (`inventory_movement`, `track_inventario`). Exposing it
unlocks the thing upstream's §1 actually deferred and neither app can do
alone: **theoretical vs. actual consumption variance**. This app knows what
a plate *should* consume; inventory knows what actually left the shelf. The
difference is waste, theft, portioning drift, or a wrong merma — and it is
the only signal that tells you *which*. This is the highest-value future
endpoint in either repo.

**B. Upstream, `/api/v1/ventas` (Phase 2).** The daily sales totals
upstream already reads from an operator-maintained Excel (`Fecha`,
`Facturas`, `Subtotal`, `Servicio`, `IVA`, `Descuento`, `Venta total`).
Enough for a whole-restaurant food-cost % (period COGS ÷ venta neta) in
Phase 2 without waiting on per-dish POS data. Cheap, and it makes Phase 2's
margin work land against a real denominator instead of a target.

**C. menu-builder's own outbound `/api/v1` (late Phase 1 or Phase 2).**
`GET /api/v1/recetas`, `GET /api/v1/recetas/{id}/costo`,
`GET /api/v1/insumos`. Nearly free once the costing engine exists, and it
keeps the dependency direction honest: **both apps only ever read each
other, never write** (non-negotiable #8 in both directions). It is also how
upstream's dashboard could eventually show a cost-per-plate card, and how a
printed recipe card, a menu site, or a POS import would consume this data
without anyone reaching into this database.

**D. One inbound write endpoint, Phase 3 only.** `POST /api/v1/ventas-mix`
— the per-dish sales export that unblocks menu engineering. Naming it now
is useful; designing it before we know what the POS can actually emit is
not (§13 Q6).

**Deliberately not an API: this app's own builder screen.** Live totals
recompute through htmx server-rendered fragments, not a JSON endpoint plus
JavaScript math. There is **one** costing engine and it is the Python one;
a JSON endpoint for the builder invites a second copy in the browser, and
the two will disagree the first time a merma changes. If the builder ever
feels slow, the fix is a faster server render, not a client-side cost model.

---

## 6. Domain model

Four nouns, and the whole app is the relations between them.

**Producto (upstream, mirrored).** What a specific supplier sells you,
under their own name, at their own price. Two suppliers' versions of the
same thing are two productos. Never edited here.

**Insumo.** What the kitchen uses. *Pechuga deshuesada* is one insumo,
sourced today from two productos and tomorrow maybe from three. Carries the
things a producto cannot know: what unit the kitchen measures it in, how
much of it is lost preparing it, and which price to believe.

**Preparación (a.k.a. insumo compuesto / subreceta).** Something made in
house that other recipes consume as if it were an insumo — a salsa, a
marinada, a fondo, a masa. It has a recipe of its own *and* a yield, and
that pair is exactly what lets it be priced per gram like any insumo.

**Receta (plato).** The menu item. Same structure as a preparación minus
the yield, plus portions.

Preparación and receta are **one table** with a `tipo`, not two — they have
identical structure, identical costing, and the only difference is that a
preparación declares a yield and a plato declares portions. Two tables would
mean two costing engines, two builders, and an eventual divergence.

### 6.1 Why merma and método de costeo live on the insumo

Both are *judgments about a purchased good*, not about a dish. The 12%
you lose deboning a pechuga is a property of pechugas, and it should not be
retyped into every recipe that uses one — that is how six recipes end up
with six different yields for the same ingredient. Same for costing method:
"price chicken at its 90-day max because it swings and I would rather
under-promise" is a stance about chicken.

The escape hatch is per-line, not per-insumo: a `receta_linea` may override
merma when a specific preparation genuinely differs (a brunoise wastes more
than a rough chop). Phase 1 ships the insumo-level field and the column for
the override; the override UI is deferred until a real case shows up.

---

## 7. Units, merma, and the costing engine

This section is the app. Everything else is a form around it.

### 7.1 Unit system

Three dimensions, and conversion happens **only within one**:

| dimension | canonical | accepted |
|---|---|---|
| masa | `g` | `mg`, `g`, `kg`, `oz`, `lb` |
| volumen | `ml` | `ml`, `l`, `gal`, `floz` |
| conteo | `unidad` | `unidad` |

`unidad` converts to nothing. A recipe line whose unit is not compatible
with its component's own unit is a **validation error at save time**, not a
silent coercion — and the error names both units.

**`oz` is mass and `floz` is volume, and they are separate units on
purpose.** A fluid ounce is 29.57 ml; an ounce is 28.35 g. Folding them
into one "oz" would make *6 oz de aceite* silently mean a mass, cost it
against a volume-based insumo, and be wrong twice — once by ~4%, and once
by the whole density question non-negotiable #6 exists to refuse.

### 7.1.1 The unit belongs to the recipe line, not to the insumo (D30)

An insumo is **costed** in one base unit — `g`, `ml` or `unidad`, the
canonical unit of its dimension — because a cost has to be per *something*,
and per-gram is the form that never needs a fraction. Everything is stored
that way: never per kg, never per litre.

**What a recipe measures in is a separate question, answered per line.**
The same aceite costed per ml can be called for in litres, millilitres or
fluid ounces; the same pechuga costed per gram in kilos, grams, pounds or
ounces. Each line carries its own unit and converts on the way into the
cost (`graph.cost_recipe`). One recipe may legitimately use `kg` on one
line and `g` on another for the same insumo.

The freedom is **within the dimension only**. `6 floz` of an insumo costed
per gram is still refused, because that needs a density nobody has
recorded — the one thing this design does not bend.

This was originally modelled the other way round: `Insumo.unidad_uso`, "the
unit the kitchen measures it in", which read as though every recipe using
pechuga had to write grams. Renamed `unidad_base` and reframed on
2026-09-02 (operator's call). The storage did not change — the field always
held a canonical unit — but the name was making a promise about recipes
that the costing engine never actually imposed.

**Density is deferred, not forgotten.** Converting `ml` of aceite to `g`
needs a per-insumo `densidad_g_por_ml`, which is a real field with a real
default nobody should invent. The column exists from day one and is
unused in Phase 1; until it is populated, cross-dimension conversion is
refused (non-negotiable #6).

### 7.2 `factor_conversion` — from a purchase unit to a use unit

Each `insumo_fuente` carries **`factor_conversion`: how many
`unidad_base` one `unidad_comercial_base` of that producto yields.**

> Buy *pechuga deshuesada* in packages the invoice describes as `1/5kg`;
> use it in grams. `unidad_comercial_base` is the package, `unidad_base` is
> `g`, `factor_conversion` is **5000**.

So the price of one unit of use, before merma:

```
precio_por_unidad_base = precio_efectivo_normalizado_crc / factor_conversion
```

It is defined against `unidad_comercial_base` and not against the invoice's
own unit precisely because upstream already normalized the price to that
base (§5.2). Getting this backwards makes every case-bought insumo wrong by
the case size, which is the single most likely way to break this app.

**Where the factor comes from, in priority order**, with the rule that
fired recorded in `factor_origen`:

| # | `factor_origen` | rule |
|---|---|---|
| 1 | `manual` | a person typed it — never overwritten by anything below (upstream D15) |
| 2 | `llm_descripcion` | the LLM read a pack size out of the description: `1/5kg` → 5000 g, `CJ 24x350ML` → 8400 ml |
| 3 | `unidad_medida` | the XML's own `UnidadMedida` is a real unit (`Kg`, `L`, `Un`) and the base unit is that unit |
| 4 | `default_categoria` | category fallback — **carne, pollo y mariscos default to `kg`**, per the operator's own rule; produce likewise |
| 5 | `sin_resolver` | nothing fired. The fuente stays unusable and shows in the mapping queue as needing a factor |

A `sin_resolver` fuente never contributes to a price series (non-negotiable
#7). It is a queue item, not a zero.

**Rule 3 will almost never fire, and that is a measurement, not a guess.**
Across the live catalog's 1 420 products, `unidad_medida` is `Unid` on 900
and `Otros` on 300 — **85% carry no usable unit at all**. Only 161 say `Kg`
(in two spellings), 52 `Gal`, and a handful the rest. `unidad_comercial` is
worse: `NULL` on 17 059 of 17 645 purchase lines (96.7%), with only `CJ`
(485), `BOT` (86) and `PZA` (15) ever populated.

Two consequences the build must plan around rather than discover:

- **The LLM's description parsing and the category default carry nearly all
  the weight.** Rules 2 and 4 are the system, not the fallbacks. Budget
  prompt quality accordingly, and make the manual factor field easy to
  reach — it is going to be used.
- **Upstream's unit normalization is largely inert for this catalog.** It
  matters for exactly the case it was built for (FEMSA and Distribuidora La
  Florida selling by `CJ` and `BOT`), and for nothing else. It still belongs
  in the contract — those products are real — but do not expect it to
  rescue a produce line, because produce arrives with no commercial unit at
  all.

### 7.3 Merma

`merma_pct` is the fraction of a purchased quantity that never reaches the
plate — bone, skin, trim, peel. Purchased cost is spread over what actually
survives:

```
costo_por_unidad_base = precio_por_unidad_base / (1 - merma_pct/100)
```

Buy chicken at ₡4 000/kg with 12% merma and the kitchen's real cost is
₡4 545/kg of usable meat. Constrained to `0 ≤ merma_pct < 95`; 95 and above
is rejected as a typo, because the asymptote turns a fat-finger into an
astronomically expensive plate rather than into an error.

**Merma is not the same thing as a preparación's yield** and the two must
never be applied to the same loss. Merma is loss on the *purchased insumo*
before it enters a recipe. Yield (§7.5) is what a *recipe* produces from
its inputs — reduction, evaporation, cooking loss. Applying both to the
same reduction double-counts it. Each surface names which one it is showing.

### 7.4 Costing methods

For an insumo, the **price series** is the union over all `confirmed`,
`activo` fuentes of every observation in the mirror, each converted to
`precio_por_unidad_base` by that fuente's own factor, sorted by `fecha`.
Observations with a `null` `precio_efectivo_crc` are dropped and counted.

The method reduces that series to one number:

| value (DB) | UI label | definition |
|---|---|---|
| `last` | Último precio | the most recent observation in the whole series |
| `avg_window` | Promedio | arithmetic mean over the window |
| `weighted_avg_window` | Promedio ponderado | mean weighted by quantity purchased — the true average paid |
| `median_window` | Mediana | robust to a single odd invoice |
| `max_window` | Máximo | conservative costing: price the plate at the worst recent case |
| `min_window` | Mínimo | mostly for comparison; rarely the right default |

`ventana_dias` (default **90**) applies to every method but `last`. The
app-wide default method and window live in Ajustes; an insumo overrides
either.

**The default is `median_window` over 90 days** (Q3, answered 2026-09-02),
and the choice was made against the live data rather than by taste:

| measurement (live catalog, 90-day window) | value |
|---|---|
| products with ≥1 purchase in the window | 396 |
| …with exactly 1 (every method gives the same answer) | 161 (41%) |
| …with ≤2 (median ≡ mean) | 213 (54%) |
| …with ≥3, where the method actually matters | 183 |
| of those, carrying ≥1 outlier (<½ or >2× the median) | **11 (6%)** |
| mean deviation from median: `weighted_avg_window` | 2.81% |
| mean deviation from median: `avg_window` | 4.75% |
| mean deviation from median: `last` | 4.16% (**max 92%**) |

The deciding case is real and named: `PEREJIL` from Inversiones Hevar V&R
has 21 purchases in the window, priced between ₡850 and ₡1 190, plus one
line at **₡120.79**. That is a bad invoice line, not a price move. A median
ignores it outright; a mean absorbs it, and on an insumo with only three
observations it absorbs it hard.

The robustness is also free where it is not needed: for the 54% of products
with two or fewer purchases in a window, the median is arithmetically
identical to every other method. `weighted_avg_window` — the honest average
actually paid, and empirically the closest alternative to the median at
2.81% — stays one setting away per insumo, for the ingredients where "what I
paid on average" is the better question than "what it typically costs".

`last` is deliberately *not* the default despite being the most intuitive:
its worst-case deviation from the median on this catalog is 92%, which is a
plate whose cost doubles because of one line.

**Two subtleties worth stating, because both are easy to get wrong:**

- `last` is the last observation *across suppliers*, not per supplier. If
  you bought from the expensive supplier yesterday and the cheap one last
  week, `last` is the expensive one. That is intended — it is what you would
  pay to cook that dish today — but it makes `last` volatile for an insumo
  with two active suppliers, which is exactly when `weighted_avg_window` is
  the better default.
- **An empty window is not an empty cost.** If the window contains no
  observation but the series is not empty, the insumo is costed at the most
  recent observation *outside* the window and flagged `precio_viejo` with
  its age in days. Silently falling back would hide a delisted ingredient;
  refusing outright would break a whole menu over one seasonal item.

### 7.5 Recipe cost

For a receta `R` with lines `L`:

```
costo_total(R) = Σ_L  cantidad_L (converted to the component's unit)
                      × costo_unitario(component_L)

costo_unitario(insumo)      = §7.3's costo_por_unidad_base
costo_unitario(preparación) = costo_total(P) / rendimiento_cantidad(P)

costo_por_porcion(plato)    = costo_total(R) / porciones(R)
```

A preparación's `rendimiento_cantidad` + `rendimiento_unidad` (2 500 g of
salsa) is what makes it consumable by the gram. Cooking loss lives here and
only here: put in 3 000 g, declare a yield of 2 500 g, and the 500 g of
evaporation is priced into every gram that survived.

**Traversal** is depth-first with memoization per request — a salsa used by
six platos is costed once. Depth is capped at **5** and cycles are detected
during traversal even though they are rejected at write time
(non-negotiable #5), because a defensive check costs nothing and a hung
page costs an evening.

The cap is 5 because the kitchen really nests three — **fondo → salsa →
plato** (Q4, answered 2026-09-02) — leaving two levels of headroom for a
preparación nobody has modelled yet while staying tight enough that a
runaway nest surfaces at the fourth or fifth hop instead of the eighth.
Raise it if a real recipe needs the room; never raise it to make a
suspicious graph load.

**Incompleteness propagates, cost does not.** If any leaf resolves to no
price, `costo_total` is `null`, the receta's state is `incompleto`, and the
UI names the offending insumos. A partial sum is not shown, not even
greyed — a number on screen is a number someone will use.

### 7.6 Explainability

Every cost figure is clickable to a **"¿Por qué este costo?"** panel:
the method and window that fired, the full series with each observation's
date, supplier, invoice and normalized price, how many observations were
dropped and why, the factor and where it came from, the merma applied, and
the mirror's as-of date. This is principle 3 made visible, and it is what
makes the operator willing to trust a number they did not compute.

### 7.7 Cost history

`receta_costo_historico` gets one row per receta per day from a scheduled
recompute, storing the cost *and* the inputs that produced it (method,
window, mirror as-of). That is what backs the cost-over-time chart, and it
is stored rather than recomputed because recomputing history from today's
mappings would rewrite the past every time a mapping changes.

The chart reuses upstream's `_stats_macros.html` card shape —
media/mediana/moda/desv./máx./mín. plus a Variación tile — computing its
stats off the same series it plots, for the same reason upstream does.

---

## 8. LLM-assisted mapping

Modelled directly on upstream's `services/llm_categorize.py`, including its
stance, which is worth restating because it is what keeps this affordable
and safe: **an accelerator, never a dependency.**

**Two jobs, one call per new producto:**

1. **Grouping** — given a producto's description and the list of existing
   insumos, either pick one or propose a new canonical name.
2. **Extraction** — read `unidad_base` and pack size out of the description
   (`1/5kg`, `CJ 24x350ML`, `BOLSA 2.5 KG`) to seed `factor_conversion`.

**The rules, inherited:**

- Called **once per new producto**, never per purchase. The live catalog
  makes this concrete (Q2, answered 2026-09-02): **1 420 products across 32
  suppliers over 17 645 purchase lines**, spanning 2024-06-05 to 2026-09-01.
  So the one-time backfill is ~1 400 calls, not ~17 600 — a ratio of 12:1,
  and the reason the "once per producto" rule is a budget decision rather
  than a style one. Steady state is a handful of calls a week.
- Called via `httpx` directly, not the `anthropic` SDK — same as upstream's
  `hacienda.py`, `exchange_rate.py` and `llm_categorize.py`, so this adds no
  dependency for one endpoint.
- **Output is validated against an offered list, never trusted raw.** For
  grouping, the answer must be an existing insumo slug or the literal
  `nuevo` plus a name. Anything else is discarded.
- **No API key, a timeout, a network failure, or an unparseable answer all
  degrade** to a deterministic fallback: normalized-string and trigram
  similarity against existing insumo names. The queue always gets *some*
  suggestion, and a brand-new producto never blocks on a billed call.
- **Every suggestion lands as `suggested`** and appears in the mapping
  queue. Non-negotiable #4: nothing reaches a cost figure without a person
  confirming it. `mapped_by` records `llm` or `human`; `mapping_confidence`
  is stored for queue ordering and is *never* used as a threshold to
  auto-confirm.

**The queue is the UX.** `/insumos/mapeo` lists unmapped productos grouped
by suggested insumo, so the operator confirms *"these four rows are all
pechuga deshuesada"* in one action rather than four. Rejecting a suggestion
is one click and is remembered, so the same wrong suggestion is not offered
again.

**And the queue must be filtered before it is drained, or it will not be
drained.** 1 420 rows is not a sitting. The live numbers say where the real
work is:

| slice | products |
|---|---|
| whole catalog | 1 420 |
| minus non-food categories (Empaques 123, Menaje 112, Limpieza 80, Utilidades 49, Higiene 36, Oficina 15) | **1 005** |
| of those, bought in the last 180 days | **412** |
| of those, bought in the last 90 days | **301** |

So the queue defaults to **food categories, bought in the last 180 days** —
about 400 rows — sorted by purchase volume, with the rest reachable behind a
filter. Combined with lazy mapping (an insumo only has to exist when a
recipe wants it), that is a drainable amount of work. Bulk confirm is
therefore a day-one requirement, not a nice-to-have: at 1 420 rows,
one-at-a-time confirmation is the difference between a queue that gets
emptied and one that gets abandoned.

---

## 9. Data model

Structural identifiers are English; the four domain nouns keep their
Spanish names (`insumo`, `receta`) because they are the operator's own
vocabulary and *insumo compuesto* has no faithful one-word English
equivalent. Enum **values** are English (`suggested`, `confirmed`,
`last`, `avg_window`), and every one of them has a Spanish UI label. See
D3.

### 9.1 Catalog mirror (projection — rebuildable, never hand-edited)

- **`catalog_product`** — `id`, `internal_code` (unique, the join key),
  `upstream_product_id`, `supplier_id`, `supplier_nombre`,
  `family_supplier_id`, `description`, `cabys`, `unidad_medida`,
  `unidad_comercial_base`, `categoria_slug`, `ignored`, `last_purchase_at`,
  `upstream_updated_at`, `synced_at`
- **`catalog_price_point`** — `id`, `catalog_product_id`, `clave`,
  `numero_linea` (unique together), `fecha`, `cantidad`,
  `unidad_comercial`, `moneda`, `precio_efectivo`, `precio_efectivo_crc`,
  `unit_ratio`, `precio_efectivo_normalizado_crc`, `iva_pct`,
  `descuento_pct`
- **`catalog_sync_run`** — `id`, `started_at`, `finished_at`, `status`,
  `watermark`, `productos_upserted`, `precios_inserted`, `error`

### 9.2 Insumos

- **`insumo`** — `id`, `nombre`, `slug` (unique), `categoria_slug`,
  `unidad_base` (`g`|`ml`|`unidad`), `densidad_g_por_ml` (nullable, unused
  in Phase 1), `merma_pct` (default 0), `metodo_costeo` (nullable → app
  default), `ventana_dias` (nullable → app default), `notas`, `activo`,
  `created_at`, `updated_at`
- **`insumo_fuente`** — `id`, `insumo_id`, `catalog_product_id`,
  `factor_conversion` (`NUMERIC`, nullable when `sin_resolver`),
  `factor_origen` (`manual`|`llm_descripcion`|`unidad_medida`|
  `default_categoria`|`sin_resolver`), `status`
  (`suggested`|`confirmed`|`rejected`), `mapped_by` (`llm`|`human`),
  `mapping_confidence` (nullable), `activo` (a confirmed source can be
  switched off without losing the mapping — a supplier you stopped buying
  from), `created_at`, `confirmed_at`
  — unique on `catalog_product_id` where `status <> 'rejected'`: one
  producto belongs to at most one insumo.
- **`insumo_mapeo_rechazo`** — `id`, `catalog_product_id`, `insumo_id`,
  `created_at`. Remembers a rejected suggestion so it is not re-offered.

### 9.3 Recetas

- **`receta`** — `id`, `nombre`, `slug` (unique), `tipo`
  (`preparacion`|`plato`), `descripcion`, `categoria_menu` (nullable),
  `porciones` (platos), `rendimiento_cantidad` + `rendimiento_unidad`
  (preparaciones), `notas_produccion`, `activo`, `created_at`, `updated_at`
  — a check constraint enforces the pairing: a `preparacion` has a
  rendimiento and no porciones, a `plato` the reverse.
- **`receta_linea`** — `id`, `receta_id`, `orden` (drag-and-drop order),
  `tipo_componente` (`insumo`|`receta`), `insumo_id` (nullable),
  `componente_receta_id` (nullable), `cantidad`, `unidad`,
  `merma_pct_override` (nullable, column shipped, UI deferred — §6.1),
  `nota`
  — a check constraint enforces exactly one of the two component FKs.
- **`receta_costo_historico`** — `id`, `receta_id`, `fecha` (unique
  together), `costo_total`, `costo_por_porcion`, `estado`
  (`completo`|`incompleto`), `metodo_costeo`, `ventana_dias`,
  `mirror_as_of`, `computed_at`

### 9.4 Settings

- **`app_setting`** — single row: `metodo_costeo_default`,
  `ventana_dias_default`, `upstream_api_url`, `moneda` (`CRC`),
  `llm_enabled`, `updated_at`

**No Alembic in Phase 1** — `create_all` plus documented manual `ALTER
TABLE`s, matching upstream's current reality *and its known debt*. Upstream
records this as debt because `create_all` never alters an existing table;
this app inherits the same trap and the same warning. Wiring Alembic here
first, and backporting it, is a legitimate Phase 2 candidate.

---

## 10. Information architecture

Sidebar, in this order:

- **Menú** (`/recetas`) — the platos. Cards or table with cost per portion,
  state (`completo`/`incompleto`), and last cost change.
- **Preparaciones** (`/preparaciones`) — sub-recipes, with a "usada en N
  recetas" column that is a link, because the first question about a salsa
  is always what breaks if you change it.
- **Insumos** (`/insumos`) — the canonical list: unidad base, merma,
  método, current cost, source count, last purchase.
  - **Mapeo** (`/insumos/mapeo`) — the queue. Carries a count badge; it is
    the only nav item that ever nags.
- **Ajustes** (`/ajustes`) — costing defaults, upstream connection and sync
  status, LLM toggle.

### 10.1 `/recetas/{id}` — the builder

The screen everything else exists to serve. Three regions:

```
+----------------------+----------------------------------------+
|  Paleta              |  Receta: Pollo a la plancha            |
|  [buscar...........] |  --------------------------------------|
|                      |  ::  Pechuga deshuesada  [180] g  ₡818 |
|  Insumos             |  ::  Aceite de oliva      [10] ml  ₡95 |
|   - Pechuga desh.    |  ::  Salsa criolla        [45] g  ₡132 |
|   - Aceite oliva     |        (preparación · ver desglose)    |
|   - Sal              |  ::  Sal                   [2] g    ₡1 |
|                      |                                        |
|  Preparaciones       |  [ soltá acá para agregar ]            |
|   - Salsa criolla    |  --------------------------------------|
|   - Marinada base    |  Costo total        ₡1 046             |
|                      |  Porciones          1                  |
|                      |  Costo por porción  ₡1 046             |
+----------------------+----------------------------------------+
```

- **Drag from the palette into the lines**, and **drag lines to reorder**.
  Both use the same vendored module. Keyboard-accessible: every draggable
  item also has an "Agregar" button and every row a move up/down control —
  drag-and-drop is the fast path, never the only path.
- **The quantity is a plain numeric input with a unit selector** next to it,
  limited to units compatible with that component. Editing it recomputes
  the line cost, the total and the per-portion figure **live** (htmx POST
  on change, server-rendered totals — one costing engine, no JS copy of the
  math, per §5.4).
- **Each line shows its own cost and its share of the total**, because
  "what is actually expensive here" is the question the screen is for.
- A **preparación line expands in place** to show its own breakdown,
  read-only, with a link to edit it.
- **`incompleto` is loud**: a banner naming each ingredient with no price
  and linking straight to it, and the total renders as `—`, never as a
  partial sum.

### 10.2 `/insumos/{id}`

Fuentes (each with supplier, description, factor, `factor_origen` badge, an
activo toggle), the configuration fields (unidad base, merma, método,
ventana), the histórico card in upstream's shape, "¿Por qué este costo?"
inline, and "usado en N recetas" as a link.

---

## 11. Phasing

### Phase 0 — this document, repo skeleton, compose up (now)

Docs, Docker Compose, config, DB bootstrap, health check, the pure modules
(`units`, `costing`, `graph`) with their unit tests. No UI beyond a stub.
**Deliverable:** `docker compose up` serves a page and `python -m unittest`
is green.

### Phase 1 — Insumos, recetas y costeo

| step | work | done when |
|---|---|---|
| 1.0 | ~~**`/api/v1` in `restaurant-manager`**~~ — **done 2026-09-02** (upstream 0.75, `56653c8`): six GET endpoints, 27 tests, upstream D28/D29, verified against the live catalog | ✅ endpoints return real rows from the live DB |
| 1.1 | ~~`catalog_sync` + mirror tables + Ajustes sync panel~~ — **done 2026-09-02**: 1 420 productos and 17 645 compras mirrored from the live API in 46 s; incremental re-sync 2 s | ✅ full catalog and price history mirrored; re-sync idempotent |
| 1.2 | ~~Insumo CRUD, unidad base, merma, método/ventana~~ — **done 2026-09-02** | ✅ an insumo can be created and configured by hand end to end |
| 1.3 | ~~Mapping queue + LLM suggestion + deterministic fallback~~ — **done 2026-09-02** | ✅ the real catalog is drainable into insumos in one sitting |
| 1.4 | ~~Costing engine wired to real data + "¿Por qué este costo?"~~ — **done 2026-09-02** | ✅ verified: app said ₡4.36/g, hand-computed SQL median said 4.3632 |
| 1.5 | `receta` / `receta_linea`, preparaciones, cycle guard | a salsa can be built and reused by two platos |
| 1.6 | The builder screen: drag-drop, live totals, incompleteness | a plate is costed from scratch in under two minutes |
| 1.7 | `receta_costo_historico` + the cost-over-time card | a chart with real daily points |

**Phase 1 is done when** the operator can cost the real menu, and every
figure on screen can explain itself.

### Phase 2 — Precio de venta y margen *(outline — needs its own conversation)*

Adds what you charge, which the app does not know yet: `precio_venta`,
`margen_bruto`, `food_cost_pct` against a per-plato or global target, and a
cost-drift alert reusing upstream's price-alert idea at the plate level
("Pollo a la plancha subió 8% en 30 días"). Also the reverse tool —
**precio sugerido** from a target food cost — which is the actual reason to
build this, and the menu-wide view of which plates are drifting.

`/api/v1/ventas` upstream (§5.4 B) lands here too, giving a real
whole-restaurant food-cost % denominator instead of a target.

**Every menu price is set as base × 1.23 — 13% IVA plus 10% servicio, both
charged on the base** (Q5, answered 2026-09-02 by the operator, then
verified against 620 days of real sales). So stripping it back is a single
constant:

```
precio_neto = precio_menu / 1.23
```

**It is 1.23 and not 1.243, and the sales file settles that decisively.**
The textbook assumption is that servicio joins the IVA base (base × 1.10 ×
1.13); it does not here:

| basis | rate | verdict |
|---|---|---|
| IVA ÷ (subtotal − descuento) | **13.00%**, on **617 of 620 days**, never above | ✅ this is the base |
| IVA ÷ subtotal | 12.92% | discount-shifted |
| IVA ÷ (subtotal + servicio) | 11.86% | ❌ servicio is *not* in the IVA base |

Two further facts that fall out, and the second one prevents a real bug:

- **Descuento is applied before IVA.** The 13.00% lands exactly on
  `subtotal − descuento`, not on `subtotal`.
- **Never apply the 1.23 divisor to aggregate sales.** Servicio runs
  **6.24% – 10.00% of subtotal day to day** (median 8.94%; it hits exactly
  10.00% on only 4 of 620 days), because a share of sales carries no
  servicio at all — takeout. The pricing rule is uniform; the *sales mix*
  is not. For the whole-restaurant food-cost denominator (§5.4 B), use the
  file's own `Subtotal − Descuento`, which is already net of both, and never
  a figure back-derived from `Venta total`.

This **reverses an earlier draft of D21**, which called for per-plato
`lleva_servicio` and `iva_pct` flags. Those were inferred from the aggregate
ratios sitting below 10% and 13% — but that gap is the sales mix, not the
pricing rule, and the fix was to ask what the operator actually does rather
than to model the variance. One constant, no flags. Should a plate ever be
priced differently, that is a new field with a real case behind it.

Still open before building: is the target food-cost a single number or per
category (§13 Q7).

### Phase 3 — Ingeniería de menú *(outline — no longer blocked)*

Popularity × profitability quadrants (estrellas / puzzles / caballos de
batalla / perros), contribution margin per plate weighted by how often it
actually sells. Plus the variance work `/api/v1/inventarios` (§5.4 A)
unlocks: theoretical consumption from recipes vs. actual movement from
inventory.

**The POS can export sales by dish** (Q6, answered 2026-09-02), which is
what this phase was waiting on. Note the data does *not* come through
`restaurant-manager`: upstream's `/ventas` reads an operator-maintained
Excel of daily totals only (Fecha, Facturas, Subtotal, Servicio, IVA,
Descuento, Venta total) with no per-dish breakdown, and its real sales
ingestion (its §6.8/§6.9) is still unbuilt. The per-dish export lands
**here**, through §5.4 D's `POST /api/v1/ventas-mix` or a file drop
mirroring how upstream reads its own spreadsheets.

The one genuinely hard problem this phase inherits is **matching an exported
dish name to a `receta`** — the same identity problem as product→insumo
mapping, one level up, and it should reuse the same shape: an LLM
suggestion, a review queue, human confirmation, and a remembered rejection.
A POS dish name drifts (`Pollo plancha`, `POLLO A LA PLANCHA`, `Pollo
Plancha c/ ensalada`) exactly the way a supplier's product description does.

Still open before building: the export's format and where the file lands
(§13 Q8).

---

## 12. Cross-cutting concerns

- **Language.** UI text in Costa Rican Spanish, voseo where natural
  ("Agregá un insumo", "Revisá la unidad"). Errors say what happened and
  what to do next; they do not apologize. Routes are Spanish (`/insumos`,
  `/recetas`) matching upstream's actual URLs.
- **Money.** CRC, `NUMERIC`, net of IVA, displayed with the `₡` symbol via
  the same `money`/`amount` filter shapes upstream uses.
- **Time.** `America/Costa_Rica`, everywhere, from the container up.
- **Colors and components.** Ported from upstream's `app.css` custom
  properties — same palette, same `.card`/`.kpi`/`.pip` vocabulary, and
  the same two-alert-palette rule (amber is a queue, red is a bad number).
  The mapping queue is amber; an `incompleto` receta is amber; a cost that
  jumped is red. `.trend--up` stays red because up is the bad way for a
  cost to move.
- **Auth.** None, same as upstream, and bound to `127.0.0.1` for the same
  reason. Do not port-forward it.
- **Testing.** `unittest`, no pytest, matching upstream. The pure modules
  (`units`, `costing`, `graph`) get real coverage including the nasty
  cases: an empty window, a `null` CRC price, a cycle, a depth-8 nest, a
  merma at the boundary, mixed units in one recipe. DB-touching tests use a
  `dbcase.py` harness like upstream's.

---

## 13. Open questions

### Answered 2026-09-02

Q1–Q6 are closed. Each answer is recorded in the section it affects rather
than only here, and the two that were settled by *measuring* rather than by
asking are marked — those are facts about the live data, and they expire if
the data changes.

| # | question | answer | where it lives |
|---|---|---|---|
| Q1 | Is `precio_efectivo_normalizado_crc` stored or computed? | **Computed** per request. `services/products.infer_unit_ratios_for_products` already exists and does a whole list in one query — use it; add no stored column *(measured)* | §5.2 |
| Q2 | How big is the catalog? | **1 420 products, 32 suppliers, 17 645 purchase lines**, 2024-06-05 → 2026-09-01. 1 005 are food; 412 bought within 180 days *(measured)* | §8 |
| Q3 | Default costing method? | **`median_window`, 90 days** — 6% of eligible products carry a bad invoice line, and for 54% the median is identical to every alternative anyway *(measured, then confirmed)* | §7.4 |
| Q4 | How deep do preparaciones nest? | **Three** (fondo → salsa → plato). Depth cap set to 5, keeping two levels of headroom | §7.5 |
| Q5 | What does a menu price include? | **13% IVA + 10% servicio, both on the base — every price, uniformly.** So `precio_neto = precio_menu / 1.23`, one constant. Servicio is *not* in the IVA base: IVA is exactly 13.00% of `subtotal − descuento` on 617 of 620 days *(operator's rule, then verified)* | §11, Phase 2 |
| Q6 | Can the POS export per-dish sales? | **Yes.** Phase 3 is unblocked; the data lands here, not through upstream | §11, Phase 3 |

### Still open

Neither blocks Phase 1; each blocks the phase it names.

1. **Q7 (blocks 2).** Is the target food-cost percentage a single
   restaurant-wide number, or per menu category? A single number is simpler
   and usually wrong — bar and food carry very different targets, and this
   catalog has 131 alcohol products against 60 carnes, so the two are not a
   rounding difference.
2. **Q8 (blocks 3).** What format does the POS's per-dish export take
   (Excel, CSV, an API), what columns does it carry, and where does the file
   land? The answer decides whether §5.4 D is a `POST` endpoint or a file
   watcher mirroring how upstream reads its own spreadsheets. Ask for one
   real export file before designing anything.
3. **Q9 (blocks 1.3, soft).** Do any of the 32 suppliers other than the two
   known parent/subsidiary pairs (Mayca, Gas Tomza) sell the same insumo
   under materially different descriptions? This does not block the build —
   the mapping queue handles it either way — but it sets expectations for
   how much the LLM grouping actually has to do.

---

## 14. Known risks

- **Factor inversion.** `factor_conversion` against the wrong unit is the
  most likely serious bug, and it fails quietly by a factor of the case
  size. Mitigation: a test fixture built from a real case-bought product,
  and the "¿Por qué este costo?" panel showing the factor and its origin on
  every screen where the cost appears.
- **Passing `precio_unitario_crc` through raw.** Nearly caught us in this
  document's own first draft: the column is `NULL` for every CRC purchase
  *by design*, so a pass-through plus a "drop the nulls" rule discards 98%
  of the catalog and leaves almost every insumo priceless. It fails as
  *silence*, not as an error — the app would simply report that nothing can
  be costed. Mitigation: the coalescing rule is written into §5.2 as a
  contract term, and step 1.1 gets a test asserting that a CRC purchase
  arrives with a non-null CRC price.
- **Upstream schema drift.** The API is a contract, but contracts erode.
  Mitigation: `/api/v1/version`, a sync smoke test that fails loudly on a
  missing field, and the contract documented in both repos rather than one.
- **Bad invoice lines costed as if they were prices.** Measured, not
  hypothetical: 6% of the products with enough history carry at least one
  line off by a factor of 3–10 (`PEREJIL` at ₡120.79 among ₡850–1 190).
  The `median_window` default absorbs the common case, but an insumo with
  three observations and one bad one is still exposed, and `last`/`max`
  are exposed by construction. Mitigation: the "¿Por qué este costo?" panel
  shows the whole series, so an outlier is visible at the point the number
  is questioned. A real outlier *flag* on the series is a fast-follow worth
  considering once the queue is drained — deliberately not built now,
  because a rule that hides data needs evidence before it earns the right.
- **LLM grouping over-merges.** *Pechuga con hueso* and *pechuga
  deshuesada* are different insumos with very different merma, and a model
  is happy to call them the same thing. Mitigation: non-negotiable #4, and
  a queue that shows the full description of every producto being grouped,
  not just the proposed name.
- **The catalog is bigger than the appetite to map it.** Mitigation: map
  lazily. An insumo only needs to exist when a recipe wants it; the queue
  is sorted by purchase volume so the ingredients that matter surface
  first.
- **Two apps, one operator.** Every hour spent here is an hour not spent
  upstream. Mitigation: the API is the only shared surface, and it is
  small enough to keep working when nobody is looking at it.

---

## 15. Productizing — shipping this to other restaurants

**The intent is to sell this eventually** (2026-09-02), and that changes
what some of the decisions above *mean* even though it changes very little
about what gets built next. Three answers bound the scope:

**The product is the suite, not this app alone** (D23). `menu-builder`
always ships together with `restaurant-manager`; it never has to source a
catalog from anywhere else. That kills a whole branch of work — no pluggable
`CatalogSource`, no manual-entry fallback, no spreadsheet importer — and the
upstream API stays the single contract. The cost is a real product
constraint, and it should be stated out loud rather than discovered by a
prospect: **the suite requires Hacienda electronic invoicing**, so its
market is Costa Rican restaurants (or wherever upstream's ingestion is
taught to work). A restaurant without e-invoices has no price history, and
with no price history this app has nothing to cost from.

**One install per restaurant, single-tenant** (D24). Each customer gets
their own Compose stack and their own database, exactly like today. No
`tenant_id` on any table, no scoping on any query, and data isolation is
physical rather than enforced in code — which is the strongest kind.

This is a **deliberate one-way door, and it is worth naming the exit cost**:
if the product ever pivots to a hosted multi-tenant SaaS, retrofitting
tenancy means a `tenant_id` on every table, a scope on every query, and the
certainty that one missed `WHERE` leaks one restaurant's costs to another.
That is a rewrite of the data-access layer, not a migration. We are walking
through that door knowingly, because per-site installs are how the customer
already runs upstream and because the alternative would double Phase 1.

**Act on none of it yet — just avoid one-way doors** (D25). Phase 1 is
built for this restaurant, on real data, because a product nobody has used
is a guess. What that costs us is only discipline, in one rule:

> **Country- and customer-specific values live in `app_setting` or
> `config.py`, never as literals inside a service module.**

### The inventory of what is Costa-Rica-specific

Kept honest here rather than rediscovered later. "Door" says how expensive
it is to open when the second customer arrives.

| assumption | where | door |
|---|---|---|
| 13% IVA + 10% servicio, servicio *outside* the IVA base (`/1.23`, D21) | Phase 2, not yet written | **Cheap — if it lands in `app_setting` from the start.** Three fields: `iva_pct`, `servicio_pct`, `servicio_en_base_iva`. Note the third is not obvious: this restaurant's own bills surprised us, so another restaurant's will too |
| Currency CRC and the `₡` symbol | `templating.py`'s `money` filter | Cheap — one filter reads a symbol from settings |
| `America/Costa_Rica` | Compose, `.env` | Cheap — already an env var |
| Metric units only (`g`/`ml`/`unidad`) | `services/units.py` `_UNITS` | Cheap — `_UNITS` is a table; `lb`/`oz` are two rows and a dimension already exists. Not a one-way door |
| Spanish-only UI, and **Spanish error text inside pure services** (`units.py`, `graph.py`) | services + templates | **Medium, and the only genuinely sticky one.** Selling anywhere Spanish-speaking costs nothing; selling outside it means extracting strings from modules that currently raise them directly. Left as-is deliberately — the market is Costa Rica and Latin America, and premature i18n would make the services harder to read for no near-term gain |
| Category slugs and the carnes/pollo/mariscos → `kg` default (§7.2 rule 4) | mapping | Cheap — it is already a rule table, and it is already per-install data |
| `median_window`/90d default (D17) | `costing.py` | Cheap, but re-read D17: it was tuned against *this* catalog's data quality. For a new customer it is a sensible default, not a law |
| No auth, bound to `127.0.0.1` | Compose, upstream too | **Medium, and it is the first thing to build before a second customer.** One login per install is enough — single-tenant means no roles, no invitations, no tenancy |

### What this does *not* change

Phase 1's plan, in full. No step gains scope, no table gains a column, and
the costing engine is untouched — cost math is not country-specific. The
`v1` in the API path (D22) is the one thing already built differently
because of this, and it was nearly free.

---

## 16. Multi-user — for both apps, one design

Today both apps assume exactly one person: no auth, bound to `127.0.0.1`,
and every action attributed to "the operator". That holds for one owner on
one laptop and stops holding twice over — once when a second person at
*this* restaurant needs access, and again at the first customer who is not
the person who commissioned the software.

**This is written here, but half of it is work in `restaurant-manager`.**
It is a suite-level design and splitting it across two documents is how the
two apps end up with two different login screens. When it is built, upstream
needs its own decision-log entry and doc updates — that repo's own rule, and
this section does not substitute for it.

### The rule that makes it safe

> **Auth ships with the network change, never after it.**

Multi-user in practice means the chef opens it on a tablet, which means
binding to `0.0.0.0` instead of `127.0.0.1`. Today the *only* thing
protecting either app is that it is unreachable from the network — upstream's
own README says so, in capitals. The moment that bind changes, "no auth"
stops being a reasonable simplification and becomes an open door onto the
restaurant's LAN. The two changes are one change, and they land in one
commit or not at all.

TLS is the open question that comes with it: a signed cookie over plain HTTP
on a shared LAN is sniffable. Either the deployment stays on a trusted
network and accepts that, or it terminates TLS locally. Decide it then, with
the actual network in front of you — but do not let it delay the auth work,
because unauthenticated-on-the-LAN is strictly worse than
authenticated-over-HTTP.

### One identity for the suite, not one per app (D26)

Two logins, on one machine, for one restaurant, is a product defect. So:

**`restaurant-manager` is the identity provider.** It is the anchor app —
it must be installed for the suite to work at all (D23), it already has
`audit_log`, and it already owns every other piece of master data. It grows
a `user` table and a login screen; `menu-builder` grows neither.

**`menu-builder` verifies a signed token locally, and never calls upstream
to authenticate.** On login, upstream sets a cookie carrying the user id,
role and an expiry, signed with an HMAC secret shared through both `.env`
files (`AUTH_SHARED_SECRET`, generated once at install). This app verifies
the signature offline.

That last property is the whole reason for this shape rather than a token
introspection call. The existing coupling degrades gracefully — if upstream
is down, the catalog mirror just ages and costing keeps working (§5.3).
**Auth-by-API-call would not degrade**: upstream down would mean nobody can
open the recipe screens, turning a soft dependency into a hard one. A
locally-verified signature keeps the failure mode where it already is.

The cookie mechanism works because **cookies are not isolated by port**: one
set for the install's hostname is sent to `:8000` and `:8001` alike. That is
usually a footgun; here it is exactly the property we want, and it is
available only because both apps are deployed on one host by design (D24).
Worth writing down precisely because it looks like an accident.

### Roles: four, and no permission matrix (D27)

Single-tenant, one restaurant, a handful of people. Roles map to jobs, not
to screens:

| role | `restaurant-manager` | `menu-builder` |
|---|---|---|
| `admin` (dueño) | everything | everything |
| `contabilidad` | AP, pagos, impuestos, servicios, gastos fijos, caja chica | read-only |
| `cocina` | read-only productos and precios | recetas, preparaciones, insumos, mapeo |
| `consulta` | read-only | read-only |

Deliberately **not** built: per-module permissions, custom roles,
invitations, OAuth/SSO, MFA, password-expiry policies. Every one of those is
a tenancy-shaped feature, and D24 says there is no tenancy. If a customer
genuinely needs a fifth role, that is a conversation, not a matrix.

### What multi-user actually buys: attribution (D28)

Login is the cost; the audit trail is the return, and it is the part worth
being deliberate about. Both apps already record *which rule fired* for
automatic decisions — the missing half is *which person* made the manual
ones:

- **Upstream:** who registered a supplier, who liquidated a payable, who
  confirmed a credit note out of the review queue, who edited a category.
- **Here:** who confirmed a product→insumo mapping, who set a merma, who
  changed a costing method, who edited a recipe. `InsumoFuente.mapped_by`
  currently distinguishes `llm` from `human`; with users it should say
  *which* human — a mis-merged insumo is invisible once it is inside a cost
  (non-negotiable #4), and "who confirmed this" is the first question when
  one surfaces.

### The one thing to do now, because it is free

Nothing above gets built in Phase 1. The single cheap precaution, in the
spirit of D25:

> **Leave room for an actor.** Where this app records that a human did
> something — `InsumoFuente.mapped_by` / `confirmed_at`, and every future
> audit row — assume a nullable `..._by_user_id` will be added beside it.
> Do not collapse "a human did this" into a boolean, and do not make
> `mapped_by` an enum that cannot carry an id later.

Retrofitting that is a nullable column and a backfill of `NULL`, which is
cheap. Retrofitting it into a design that recorded only `True` is an
archaeology problem.

---

## 17. Decision log

| # | Decision | Rationale | Date | Status |
|---|---|---|---|---|
| D1 | Separate app, not a module in `restaurant-manager` | Different questions, different consumers; one shared surface (the price series) is a contract, not a coupling | 2026-09-02 | Confirmed |
| D2 | Same stack as upstream (FastAPI/Jinja/htmx/Postgres), no build step | One operator maintains one stack; conventions transfer verbatim; must run with the internet down | 2026-09-02 | Confirmed |
| D3 | Domain nouns Spanish (`insumo`, `receta`); enum values and structural identifiers English | *Insumo compuesto* has no faithful English equivalent and is the operator's own word; enums stay English so they read like every other status in both apps | 2026-09-02 | Confirmed |
| D4 | Integration via a read-only `/api/v1` in upstream, shipped first | Consuming upstream's `effective_price`/`infer_unit_ratios`/family-merge beats reimplementing them; drift between two copies of the cost math is a silently wrong food cost | 2026-09-02 | Confirmed |
| D5 | Local catalog mirror as a rebuildable projection | Live costing needs fast joins across hundreds of insumos; the mirror is derived, disposable, and keyed by upstream's own natural keys so re-sync is idempotent | 2026-09-02 | Confirmed |
| D6 | Product→insumo mapping is human-confirmed; the LLM only suggests | Inherited from upstream D4 — no stable cross-supplier identifier exists, and a wrong merge is invisible once it is inside a cost | 2026-09-02 | Confirmed |
| D7 | Merma and método de costeo live on the insumo, not the recipe line | Both are judgments about a purchased good; per-recipe copies diverge. Per-line merma override column ships now, UI deferred | 2026-09-02 | Confirmed |
| D8 | `preparacion` and `plato` are one table with a `tipo` | Identical structure and identical costing; two tables would mean two engines and an eventual divergence | 2026-09-02 | Confirmed |
| D9 | Costs are net of IVA on invoice date | Upstream D6 unchanged — food cost is a margin question, not a cash question | 2026-09-02 | Confirmed |
| D10 | No cost is shown for an incomplete recipe — `—`, never a partial sum | A number on screen is a number someone will use | 2026-09-02 | Confirmed |
| D11 | Cross-dimension unit conversion refused without an explicit density | An implicit g↔ml is wrong by up to 20% and invisible; the column ships unused rather than the conversion shipping wrong | 2026-09-02 | Confirmed |
| D12 | An empty costing window falls back to the newest observation outside it, flagged `precio_viejo` | Silence would hide a delisted ingredient; refusal would break a menu over one seasonal item | 2026-09-02 | Proposed |
| D13 | Cost history is stored daily, not recomputed | Recomputing from today's mappings would rewrite the past whenever a mapping changes | 2026-09-02 | Confirmed |
| D14 | This app never writes to `restaurant-manager`, and upstream never writes here | Each app owns its master data; a second writer makes both audit trails untrustworthy. Both directions are read-only by design (§5.4 C) | 2026-09-02 | Confirmed |
| D15 | No Alembic in Phase 1 — `create_all` + documented manual `ALTER TABLE` | Matches upstream's reality *and* inherits its documented debt, knowingly. Wiring Alembic in both repos is a Phase 2 candidate | 2026-09-02 | Proposed |
| D16 | The builder screen has no JSON endpoint; live totals are server-rendered htmx fragments | One costing engine. A JSON endpoint invites a second copy of the math in the browser, and the two diverge the first time a merma changes | 2026-09-02 | Confirmed |
| D17 | Default costing method is `median_window` over 90 days, not the average | 6% of products with ≥3 purchases in a window carry a bad invoice line (PEREJIL at ₡120.79 among ₡850-1 190); a median ignores it and a mean absorbs it. Free where it is not needed: for the 54% of products with ≤2 purchases per window the median is arithmetically identical to every alternative. `weighted_avg_window` stays one setting away | 2026-09-02 | Confirmed (measured, then confirmed by the operator) |
| D18 | Traversal depth capped at 5, not 8 | The kitchen nests three (fondo → salsa → plato); 5 leaves two levels of headroom while surfacing a runaway nest at the fourth hop rather than the eighth | 2026-09-02 | Confirmed |
| D19 | The API coalesces the CRC price; it never passes `precio_unitario_crc` through raw | That column is NULL for every CRC purchase by design — 17 272 of 17 645 live rows. A pass-through plus "drop the nulls" would discard 98% of the catalog and fail as silence rather than as an error. A NULL `moneda` (310 rows) is treated as CRC, per Hacienda's own default | 2026-09-02 | Confirmed |
| D20 | The mapping queue defaults to food categories bought within 180 days (~400 of 1 420 rows), sorted by purchase volume, with bulk confirm on day one | 1 420 rows is not a sitting, and a queue that cannot be drained gets abandoned. Non-food categories (Empaques, Menaje, Limpieza, Higiene, Oficina, Utilidades — 415 products) are never recipe ingredients | 2026-09-02 | Confirmed |
| D22 | `/api/v1` keeps its path version, and a breaking payload change means `/api/v2` rather than an edit in place | Cargo-cult for a single local consumer — you would just change both repos at once. Real once the suite ships to other restaurants (2026-09-02): two customers means two independently-updated installs, and no one can force a simultaneous upgrade across sites. Far cheaper to include now than to retrofit across deployed installs. `GET /api/v1/version` does the *other* job — the minor revision the sync fails loudly on | 2026-09-02 | Confirmed |
| D23 | The product is the **suite**: `menu-builder` always ships with `restaurant-manager` and never sources a catalog elsewhere | Kills a whole branch of work (no pluggable `CatalogSource`, no manual entry, no importer) and keeps the upstream API as the single contract. Accepted cost, stated plainly: the suite requires Hacienda electronic invoicing, so the market is Costa Rica. No e-invoices means no price history means nothing to cost from | 2026-09-02 | Confirmed |
| D24 | One install per restaurant, **single-tenant**. No `tenant_id` anywhere | Matches how the customer already runs upstream; isolation is physical rather than code-enforced, which is the strongest kind. A deliberate one-way door: a SaaS pivot would mean a tenant column on every table, a scope on every query, and one missed `WHERE` leaking one restaurant's costs to another — a rewrite of the data-access layer, not a migration. Walked through knowingly, because the alternative doubles Phase 1 | 2026-09-02 | Confirmed |
| D25 | Build Phase 1 for this restaurant; act on productization only by **avoiding one-way doors** | A product nobody has used is a guess. The whole cost is one discipline — country- and customer-specific values live in `app_setting`/`config.py`, never as literals inside a service module. §15 inventories every CR-specific assumption and prices the door on each | 2026-09-02 | Confirmed |
| D26 | Multi-user: `restaurant-manager` is the identity provider; `menu-builder` verifies a signed cookie **offline** with a shared HMAC secret | One login for the suite — two would be a product defect. Offline verification keeps the failure mode where it already is: upstream down currently means the mirror ages and costing still works, whereas auth-by-API-call would mean nobody can open a recipe. Relies on cookies not being port-isolated across one host, which is available only because D24 puts both apps on one machine | 2026-09-02 | Proposed |
| D27 | Four fixed roles (`admin`, `contabilidad`, `cocina`, `consulta`), no permission matrix | Roles map to jobs, not screens. Per-module permissions, custom roles, invitations, SSO and MFA are all tenancy-shaped features, and D24 says there is no tenancy | 2026-09-02 | Proposed |
| D28 | Auth ships **in the same commit** as the `0.0.0.0` bind, never after | Today the only thing protecting either app is being unreachable from the network. The bind change is what turns "no auth" from a simplification into an open door onto the restaurant's LAN, so the two are one change | 2026-09-02 | Confirmed |
| D29 | Leave room for an actor now: never collapse "a human did this" into a boolean | The only free precaution multi-user needs in Phase 1. Adding a nullable `..._by_user_id` later is a column and a `NULL` backfill; recovering *who* from a design that stored only `True` is archaeology | 2026-09-02 | Confirmed |
| D30 | An insumo is **costed** in a canonical base unit (`g`/`ml`/`unidad`), and the **recipe line chooses its own unit** and converts | A cost has to be per something, and per-gram never needs a fraction — but nothing about that should force a recipe to write grams. An aceite costed per ml can be called for in L, ml or fl oz; a pechuga costed per gram in kg, g, lb or oz, and two lines of one recipe may differ. Freedom within the dimension only: crossing mass and volume still needs a density (non-negotiable #6). Supersedes the original `Insumo.unidad_uso` framing, which read as "the unit the kitchen must measure in" — the storage was always canonical, but the name promised something about recipes that the costing engine never imposed. Renamed `unidad_base`, live column altered by hand (no Alembic, D15) | 2026-09-02 | Confirmed |
| D31 | `oz` (mass, 28.35 g) and `floz` (volume, 29.57 ml) are separate units | Folding them would make "6 oz de aceite" silently mean a mass, cost it against a volume-based insumo, and be wrong both by ~4% and by the dimension. The catalog already uses both senses | 2026-09-02 | Confirmed |
| D21 | Phase 2 strips an all-in menu price with **one constant, `/1.23`** — every price is set as base + 13% IVA + 10% servicio, both on the base | Operator's own pricing rule, verified against 620 days: IVA is exactly 13.00% of `subtotal − descuento` on 617 of them and never above, while IVA ÷ (subtotal + servicio) is 11.86% — so servicio is not in the IVA base and the divisor is 1.23, not 1.243. **Supersedes this row's first draft**, which called for per-plato `lleva_servicio`/`iva_pct` flags: that was inferred from aggregate ratios below 10%/13%, and the gap is the sales mix (takeout carries no servicio), not the pricing rule. Corollary: never apply `/1.23` to aggregate sales — servicio there ranges 6.24-10.00% of subtotal daily; use the file's own `Subtotal − Descuento` | 2026-09-02 | Confirmed (revised same day) |
