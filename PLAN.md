# Menu Builder — Design Plan

**Status:** Phase 0 (this document + repo skeleton). Nothing is built yet.
Phase 1 is fully designed below; Phases 2 and 3 are outlined deliberately
short — they are real commitments about *direction*, not finished designs,
and each needs its own conversation before it is built.

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
     |  costing engine (pure) --> costo por unidad de uso        |
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
| `precio_efectivo_crc` | the same figure in CRC at that date's rate; `null` if no rate was available — **never a guess** |
| `unit_ratio` | how many base units one `unidad_comercial` is worth |
| `precio_efectivo_normalizado_crc` | `precio_efectivo_crc / unit_ratio` — **the figure this app costs from** |
| `iva_pct`, `descuento_pct` | for display and audit |
| `clave`, `numero_linea` | the natural key back to the invoice |

**Three properties this app depends on and upstream must not quietly
change:**

- `precio_efectivo_normalizado_crc` is expressed in `unidad_comercial_base`,
  *not* in whatever unit a given invoice happened to use. This is what makes
  a case purchase comparable to a bottle purchase, and it is why
  `factor_conversion` in this app is defined against the base unit (§7.2).
- Notas de crédito are already excluded — upstream's non-negotiable #5 means
  their lines never became `ProductPurchase` rows at all. This app must not
  try to net them again.
- `precio_efectivo_crc` is `null`, not 0 and not the USD figure, when no
  exchange rate existed for that date. Observations with a `null` here are
  **dropped from the series**, and the count of dropped observations is
  shown on the insumo's cost panel.

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
| masa | `g` | `mg`, `g`, `kg` |
| volumen | `ml` | `ml`, `l` |
| conteo | `unidad` | `unidad` |

`unidad` converts to nothing. A recipe line whose unit is not compatible
with its component's own unit is a **validation error at save time**, not a
silent coercion — and the error names both units.

**Density is deferred, not forgotten.** Converting `ml` of aceite to `g`
needs a per-insumo `densidad_g_por_ml`, which is a real field with a real
default nobody should invent. The column exists from day one and is
unused in Phase 1; until it is populated, cross-dimension conversion is
refused (non-negotiable #6).

### 7.2 `factor_conversion` — from a purchase unit to a use unit

Each `insumo_fuente` carries **`factor_conversion`: how many
`unidad_uso` one `unidad_comercial_base` of that producto yields.**

> Buy *pechuga deshuesada* in packages the invoice describes as `1/5kg`;
> use it in grams. `unidad_comercial_base` is the package, `unidad_uso` is
> `g`, `factor_conversion` is **5000**.

So the price of one unit of use, before merma:

```
precio_por_unidad_uso = precio_efectivo_normalizado_crc / factor_conversion
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

### 7.3 Merma

`merma_pct` is the fraction of a purchased quantity that never reaches the
plate — bone, skin, trim, peel. Purchased cost is spread over what actually
survives:

```
costo_por_unidad_uso = precio_por_unidad_uso / (1 - merma_pct/100)
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
`precio_por_unidad_uso` by that fuente's own factor, sorted by `fecha`.
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

costo_unitario(insumo)      = §7.3's costo_por_unidad_uso
costo_unitario(preparación) = costo_total(P) / rendimiento_cantidad(P)

costo_por_porcion(plato)    = costo_total(R) / porciones(R)
```

A preparación's `rendimiento_cantidad` + `rendimiento_unidad` (2 500 g of
salsa) is what makes it consumable by the gram. Cooking loss lives here and
only here: put in 3 000 g, declare a yield of 2 500 g, and the 500 g of
evaporation is priced into every gram that survived.

**Traversal** is depth-first with memoization per request — a salsa used by
six platos is costed once. Depth is capped at **8** and cycles are detected
during traversal even though they are rejected at write time
(non-negotiable #5), because a defensive check costs nothing and a hung
page costs an evening.

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
2. **Extraction** — read `unidad_uso` and pack size out of the description
   (`1/5kg`, `CJ 24x350ML`, `BOLSA 2.5 KG`) to seed `factor_conversion`.

**The rules, inherited:**

- Called **once per new producto**, never per purchase. Call volume tracks
  distinct products (~hundreds), not invoice lines (~tens of thousands).
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
  `unidad_uso` (`g`|`ml`|`unidad`), `densidad_g_por_ml` (nullable, unused
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
- **Insumos** (`/insumos`) — the canonical list: unidad de uso, merma,
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
activo toggle), the configuration fields (unidad de uso, merma, método,
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
| 1.0 | **`/api/v1` in `restaurant-manager`** — router, decision-log entry, PLAN/README/CLAUDE updates, smoke tests | endpoints return real rows from the live DB |
| 1.1 | `catalog_sync` + mirror tables + Ajustes sync panel | full catalog and price history mirrored; re-sync idempotent |
| 1.2 | Insumo CRUD, unidad de uso, merma, método/ventana | an insumo can be created and configured by hand end to end |
| 1.3 | Mapping queue + LLM suggestion + deterministic fallback | the real catalog is drainable into insumos in one sitting |
| 1.4 | Costing engine wired to real data + "¿Por qué este costo?" | an insumo's cost matches a hand-computed figure from the same invoices |
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

Open before building: does the plate price include IVA and servicio (CR
menus quote all three ways), and is the target food-cost a single number or
per category. Both change the model, so neither is assumed here.

### Phase 3 — Ingeniería de menú *(outline — blocked on data)*

Popularity × profitability quadrants (estrellas / puzzles / caballos de
batalla / perros), contribution margin per plate weighted by how often it
actually sells. Plus the variance work `/api/v1/inventarios` (§5.4 A)
unlocks: theoretical consumption from recipes vs. actual movement from
inventory.

**Blocked, and honestly so.** Menu engineering needs per-dish sales mix,
which does not exist in either app: upstream's `/ventas` reads an
operator-maintained Excel of *daily totals* (Fecha, Facturas, Subtotal,
Servicio, IVA, Descuento, Venta total), with no per-dish breakdown, and
upstream's real sales ingestion (its §6.8/§6.9) is unbuilt. So Phase 3
begins with a question that is not a menu-builder question at all: can the
POS export a per-dish sales report, and where does it land. Until that is
answered, Phase 3 is a placeholder and should not be designed further.

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

Answer before the step they belong to; none blocks Phase 0.

1. **Q1 (blocks 1.0).** Does upstream expose `precio_efectivo_normalizado_crc`
   as a stored figure or compute it per request? It is derived today
   (`infer_unit_ratios` runs over a product's whole history), so the
   endpoint recomputes — fine at catalog scale, worth confirming before
   the sync pulls thousands of rows.
2. **Q2 (blocks 1.3).** How many distinct productos are actually in the
   live catalog? It sets the LLM budget and decides whether the mapping
   queue needs bulk actions on day one or can start one-at-a-time.
3. **Q3 (blocks 1.4).** Default costing method: `weighted_avg_window` over
   90 days is the recommendation (honest average paid, robust to a single
   odd invoice, stable with two suppliers). Confirm, or say which stance
   the kitchen actually wants.
4. **Q4 (blocks 1.5).** Do any preparaciones nest more than two deep in
   practice? Depth 8 is a guess; if the real answer is 2, the cap can be
   tighter and the UI simpler.
5. **Q5 (blocks 2).** Does a menu price quote net, +IVA, or +IVA+servicio?
6. **Q6 (blocks 3).** Can the POS export per-dish sales?

---

## 14. Known risks

- **Factor inversion.** `factor_conversion` against the wrong unit is the
  most likely serious bug, and it fails quietly by a factor of the case
  size. Mitigation: a test fixture built from a real case-bought product,
  and the "¿Por qué este costo?" panel showing the factor and its origin on
  every screen where the cost appears.
- **Upstream schema drift.** The API is a contract, but contracts erode.
  Mitigation: `/api/v1/version`, a sync smoke test that fails loudly on a
  missing field, and the contract documented in both repos rather than one.
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

## 15. Decision log

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
