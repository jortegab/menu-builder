# Menu Builder

Recipe building and real-time menu costing for a single-site restaurant in
Costa Rica. Locally hosted, Spanish UI, no auth, one operator.

It answers one question — *what does this plate cost me today* — and its
prices come entirely from **`restaurant-manager`**, the same operator's
e-invoice ingestion app, over a read-only JSON API. This app never
recomputes upstream's price math and never writes to it.

**Read `PLAN.md` before proposing architecture.** It has the full design,
29 numbered decisions with rationale, 8 non-negotiables, and the open
questions. Do not redesign what is already decided there — if you think a
decision is wrong, say so and argue it, but do not silently deviate.

**The app's own name is the one deliberate exception to Spanish UI text**:
`main.py`'s FastAPI title is the English "Menu Builder", so the app, the
repo and the Compose project carry one name. Same exception
`restaurant-manager` makes, for the same reason. Not licence for English
anywhere else in the UI.

---

## Status

**Phase 1, step 1.5.** The catalog mirror, insumo CRUD, the mapping queue
and the costing engine — pure *and* wired to real data. Insumos cost real
numbers now; recipes do not exist yet.
Screens: `/insumos`, `/insumos/mapeo`, `/ajustes`, plus a stub `/recetas`.

**Step 1.0 is done** (2026-09-02): `restaurant-manager` 0.75 ships
`/api/v1` — six read-only GET endpoints over the catalog and price history,
27 tests, verified against the live data. The contract lives in `PLAN.md`
§5 *and* in that repo's own `routers/api.py` docstring; when they disagree,
the router is the truth and this plan is the bug.

Two things it confirmed that matter for the next step:

- **`precio_efectivo_crc` is coalesced upstream**, so a CRC purchase
  arrives with a real CRC price. `catalog_sync` must *not* re-derive it.
- **Money arrives as a string** (`"1039.6040"`), never a JSON number. Parse
  it with `Decimal(...)`, never `float(...)`.

**Steps 1.0 and 1.1 are done** (2026-09-02). `services/catalog_client.py` (network)
and `services/catalog_sync.py` (DB) fill the mirror, with `/ajustes` as the
control panel. Proven against the live API: **1 420 productos and 17 645
compras in 46 s**, incremental re-sync in ~2 s, zero null CRC prices across
every row.

Three properties of the sync are load-bearing, and each has tests:

- **Idempotent by construction, not by care.** Products key on
  `internal_code` and price points on `(clave, numero_linea)` — upstream's
  own natural keys — so a re-run writes the same rows and changes nothing.
  `full=True` re-walks everything and is always a safe repair.
- **The watermark is derived from the mirror** (`max(upstream_updated_at)`),
  never from a wall clock. Re-fetching a boundary row is free; assuming two
  machines agree about "now" is not.
- **A failed sync is not an error state.** `sync_catalog` never raises for
  anything upstream does — it records the run, leaves the mirror alone, and
  costing carries on against slightly older data. Only `mirror_as_of`
  advances on success, so a stale figure can be shown as stale.

**Step 1.2 is done too** (2026-09-02): insumo CRUD at `/insumos` —
create, edit, archive, search, and the three configuration fields.
`services/insumos.py` owns the rules.

**Validation lives in the service, not in the CHECK constraints.** Those
stay as the last line of defence, but a constraint violation arrives as an
opaque `IntegrityError` at commit time — the operator sees a 500 and their
half-filled form is gone. `ValidationError` carries field-keyed Spanish
messages so the template can put each one beside its own input, and the
form re-renders with what they typed still in it.

Three rules worth knowing before touching that module:

- **`unidad_uso` is only `g`/`ml`/`unidad`** — the canonical unit of each
  dimension. `kg` would work arithmetically and read badly on every recipe
  (0.18 kg of pechuga), so it is not offered.
- **A comma is a valid decimal separator.** Costa Rica writes `12,5`; a
  form that rejects it is a form fighting its user.
- **`metodo_costeo`/`ventana_dias` NULL means "follow the app default"**,
  not "unset". An insumo that never had an opinion tracks the global
  setting as it changes rather than freezing at creation time.

Archiving never deletes: an insumo can be referenced by a `receta_linea`,
and deleting one would orphan a recipe or silently change a plate cost.

**Step 1.3 is done too** (2026-09-02): the mapping queue at
`/insumos/mapeo`, with a nav badge that is the only thing in the app that
nags. Suggestions come from Claude when a key is configured and from
`insumo_match` when not; the queue works either way and says which.

Four things load-bearing enough to re-read before touching it:

- **`insumo_match` is not really a fallback.** 85% of the catalog has no
  usable `unidad_medida`, so the pack size is in the description text or
  nowhere. Its deterministic reading resolves a conversion factor for
  **76% of the live catalog** before any LLM runs.
- **The over-merge guard is conflict pairs, not significant words.**
  `PECHUGA CON HUESO` scored 0.47 against `Pechuga deshuesada` and was
  offered; those differ by ~30 points of merma. The weaker rule (penalise
  any qualifier one side lacks) also throws away `PECHUGA DE POLLO
  DESGUESADA`, a real typo that is the same ingredient. Conflicts fire
  only when both sides say opposite things.
- **The slash form and the x form are not the same.** `1/5KG` is one pack
  OF 5 kg; `24x350ML` is 24 TIMES 350 ml. Getting them backwards is a
  factor-of-N error on every case-bought insumo.
- **`confirmar` is only ever reached from a person**, and always writes
  `status=confirmed`, `mapped_by=human`. The suggestion path never calls
  it (non-negotiable #4).

**Step 1.4 is done too** (2026-09-02): `services/insumo_costs.py` turns
confirmed sources into a cost, and `/insumos/{id}` shows the work.

`costing.py` owns the arithmetic and stays pure; this module owns the
*gathering*, which is where the judgement calls are. Three exclusions, and
all three are **reported on screen, never silently applied** -- an insumo
that costs less than it should because half its sources were skipped is
exactly what the panel exists to reveal:

- Only `confirmed` **and** `activo` sources contribute. A suggestion is not
  evidence; a deactivated source is a supplier you stopped buying from, and
  keeping their old prices would quietly hold the cost down.
- A source with no `factor_conversion` contributes nothing. **Never treated
  as 1** -- that costs the insumo per package instead of per gram, which
  reads as a plausible number.
- An observation with no CRC price is dropped and counted.

Observations outside the window are **shown and marked, not hidden**: "why
is this using only two purchases?" is the question the table has to answer.

Verified against live data end to end: the app rendered ₡4.36/g for an
insumo fed by four suppliers, and the hand-written SQL median over the same
23 in-window observations returned 4.3632.

**Next: step 1.5** — `receta`/`receta_linea`, preparaciones, and the cycle
guard on write. `graph.py` already has the traversal and cycle detection,
unused so far.

este costo?".


Do not describe unbuilt features as working, in the UI or in the docs.
README's "qué va a hacer" section is explicitly labelled as unbuilt; keep
it that way.

---

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 · Jinja2 + htmx ·
Docker Compose — deliberately identical to `restaurant-manager` (D2), so
conventions transfer verbatim between the two repos.

```bash
docker compose up --build        # http://localhost:8001
PYTHONPATH=backend python -m unittest discover -s tests -v
```

**Ports are 8001 and 5434**, not 8000 and 5433 — upstream holds those, and
both apps run on the same machine at the same time.

**No build step, and no CDN.** Drag-and-drop will be a vendored,
dependency-free module in `static/vendor/`. This app runs on the
restaurant's own machine and must work with the internet down.

---

## Where things are

```
backend/app/
  main.py           app wiring, `/` -> `/recetas`, `/salud`
  config.py         env-driven settings
  db.py             engine, session, init_db. `get_db` publishes its
                    Session on `request.state.db` -- template code reads it
                    from there and never opens its own
  templating.py     Jinja env + the money/cantidad/pct filters and
                    mtime-versioned `static_url`. **`money(None)` renders an
                    em dash, never ₡0** -- that is D10 enforced at the last
                    possible layer
  models.py         the 10 tables of PLAN.md §9, in three layers: the
                    catalog mirror (`catalog_*`), insumos, recetas
  services/
    units.py        unit conversion. Pure, no imports beyond Decimal
    costing.py      price series -> one price, then merma. Pure
    catalog_client.py  httpx client for upstream /api/v1. Network only
    insumo_costs.py sources -> price series -> cost, plus the Explicacion
                    the "¿Por qué este costo?" panel renders
    insumo_match.py insumo matching, pure: pack-size parsing, similarity,
                    the conflict guard. Resolves 76% of factors alone
    llm_insumo.py   Claude suggestion. Accelerator, never a dependency
    mapeo.py        the mapping queue: pendientes, sugerir, confirmar
    insumos.py      insumo master data + validation. ValidationError
                    carries field-keyed Spanish messages
    catalog_sync.py    fills the mirror. Idempotent; never raises for
                    anything upstream does
    graph.py        recipe DAG: cycle detection, depth-limited traversal,
                    cost aggregation. Pure
  routers/
    ajustes.py      /ajustes: the sync panel. Sync runs synchronously in
                    the request -- deliberate for a local single-operator
                    app (~2s incremental), not an oversight
    insumos.py      /insumos: list, create, edit, archive
    mapeo.py        /insumos/mapeo. Registered BEFORE insumos.py or
                    /insumos/{insumo_id} captures it and 422s
    recetas.py      Phase 0 stub
tests/              unittest. dbcase.py = SQLite harness + FakeCatalogClient
PLAN.md             the design. 29 decisions, 8 non-negotiables
```

**`units.py`, `costing.py` and `graph.py` are pure on purpose** — no DB, no
network, no settings import — the same way upstream keeps `currency.py`,
`unit_conversion.py` and `period_keys.py` importable without a driver. The
whole of PLAN.md §7 is testable without Postgres, and it is.

**The dependency runs one way and must keep running one way.** The
DB- and network-facing modules (`catalog_sync.py`, `catalog_client.py`, and
`insumo_costs.py` when it lands) import the pure ones; never the reverse.
The moment `costing.py` needs a `Session` to do its job, the cost math stops
being checkable without infrastructure — which is exactly the property that
makes it trustworthy.

`catalog_client.py` is split from `catalog_sync.py` for the same reason
upstream splits `gmail.py` from `gmail_match.py`: the sync's decisions —
what to upsert, what to skip, how the watermark moves — are tested against
a fake client, with no `httpx` anywhere near them.

---

## Non-negotiables

Breaking one produces a plausible-looking cost that is wrong, which is
worse than an error. The full text with rationale is `PLAN.md` §3.

1. **Money is `NUMERIC`, never float.** Every cost is CRC and says so.
2. **Costs are net of IVA, on invoice date** (upstream D6). Food cost is a
   margin question, not a cash question — do not "improve" it by switching
   to the gross figure upstream's dashboard shows.
3. **The catalog mirror is derived and disposable.** `catalog_product` and
   `catalog_price_point` are rebuildable from the API. Never hand-edited,
   and nothing may live only there.
4. **A mapping is never active until a human confirms it.** LLM output
   lands as `suggested`. `mapping_confidence` orders the queue and is
   *never* a threshold that auto-confirms.
5. **The recipe graph is acyclic.** Rejected on write with the path named;
   defended again on read.
6. **Unit conversion never crosses dimensions.** No implicit g↔ml.
7. **An insumo with no usable price has no cost.** `None`, never 0.
8. **This app never writes to `restaurant-manager`**, and upstream never
   writes here.

---

## Conventions

- **UI text in Spanish; code, DB columns and comments in English** — with
  the honest caveat that **domain nouns stay Spanish** (`insumo`, `receta`,
  `merma`, `rendimiento`), because they are the operator's own vocabulary
  and *insumo compuesto* has no faithful one-word English equivalent (D3).
  Upstream's schema reads the same way (`precio_unitario_neto`, `detalle`).
  **Enum values are English** (`suggested`, `confirmed`, `last`,
  `avg_window`, `preparacion`/`plato` being the domain-noun exception), each
  with a Spanish UI label defined in the template, not in the service.
- **Routes are Spanish** (`/insumos`, `/recetas`, `/preparaciones`,
  `/ajustes`), matching upstream's actual URLs.
- Costa Rican Spanish, voseo where natural ("Agregá un insumo", "Revisá la
  unidad"). Errors state what happened and what to do next; they do not
  apologize.
- Colors come from the custom properties in `static/app.css`, ported from
  upstream. **Do not introduce new hex values inline.**
- **Two alert palettes, and which one you want depends on what the thing
  is** — inherited verbatim from upstream:
  - **Amber is a queue** — something waits for the operator and clears when
    they act. The mapping queue's badge; an `incompleto` receta's banner.
  - **Red is a number that is bad** — nothing pending, just "this figure is
    unusual". A cost that jumped.
  - `.trend--up` (red) / `.trend--down` (teal) is **direction on a cost**,
    not a severity tier: up is the bad way for a cost to move. A red
    Variación is a number, not a task.
- **Every automatic decision records which rule fired.** `factor_origen` on
  a fuente, `metodo_costeo` on a cost result, `mapped_by` on a mapping.
  This is not debug metadata — it is what the "¿Por qué este costo?" panel
  renders, and it is why the operator trusts a number they did not compute.
- **An error message names both sides.** "No se puede convertir de ml a g"
  is actionable; "unidad inválida" is not. `IncompatibleUnits` carries both
  units and `RecipeCycle` carries the path for exactly this reason.

---

## Things that are easy to get wrong here

- **`factor_conversion` is defined against `unidad_comercial_base`**, not
  against whatever unit a given invoice happened to use. Upstream already
  normalized the price to that base unit (its `infer_unit_ratios`, the
  CJ-case vs BOT-bottle problem). Getting this backwards makes every
  case-bought insumo wrong by the case size, silently. It is `PLAN.md`
  §14's first named risk.
- **Merma and rendimiento are different losses and must never be applied to
  the same one.** Merma is loss on the purchased insumo *before* it enters
  a recipe (bone, trim, peel). Rendimiento is what a recipe *produces* from
  its inputs (reduction, evaporation). Applying both to one reduction
  double-counts it.
- **`last` is the last observation across suppliers**, not per supplier.
  Intended — it answers "what would this cost to cook today" — but it makes
  `last` volatile for a two-supplier insumo, which is when
  `weighted_avg_window` is the better default.
- **`price_from_series` returning a value with `precio_viejo=True` is not a
  bug** (D12). An empty window with a non-empty series falls back to the
  newest observation outside it and flags its age. Silence would hide a
  delisted ingredient; refusal would break a menu over one seasonal item.
- **Never pass upstream's `precio_unitario_crc` through raw** (D19). That
  column is `NULL` for *every CRC purchase by design* — 17 272 of 17 645
  live rows. The API coalesces it: the CRC price is the converted figure for
  a USD line and `precio_efectivo` itself for a CRC or `NULL`-currency line.
  A pass-through plus a "drop the nulls" rule discards 98% of the catalog,
  and it fails as **silence** — the app just reports that nothing can be
  costed. In the coalesced payload a `null` therefore means exactly one
  thing: a USD line whose date had no exchange rate. Drop those, count the
  drop, and never substitute the USD figure or 0.
- **`unidad_medida` is almost never usable.** 85% of the live catalog says
  `Unid` or `Otros`, and `unidad_comercial` is `NULL` on 96.7% of purchase
  lines. §7.2's fallback rule 3 will rarely fire: the LLM's description
  parsing and the category default are the system, not the fallbacks. Do not
  build as though the XML units will carry the load.
- **A bad invoice line is not a price move.** 6% of products with enough
  history carry one (PEREJIL at ₡120.79 among ₡850–1 190). This is why the
  default method is `median_window` (D17) — do not "simplify" it to an
  average or to `last` without re-reading that decision.
- **An incompatible unit in a costed line raises**, it does not resolve to
  "no price". It means a row was written that save-time validation should
  have refused, and it must not send the operator looking in the wrong
  place.

---

## Testing

`unittest`, no pytest, matching upstream. 175 tests today, mostly against the
three pure modules.

Coverage is deliberately weighted toward the nasty cases rather than the
happy path, because the happy path is the one that gets exercised by hand
anyway: an empty window, a series with zero weights, a merma at the
boundary, a diamond that is not a cycle, a cycle that survived to read
time, a depth-limit overflow, mixed units in one recipe, and
incompleteness propagating up through a nested preparación.

When DB-touching code arrives, add a `tests/dbcase.py` harness like
upstream's rather than mocking the session.

---

## This is meant to become a product

The suite (`restaurant-manager` + `menu-builder`, always together — D23) is
intended to ship to other restaurants. Phase 1 is still built for **this**
restaurant on **real** data, and productization is acted on only by
**avoiding one-way doors** (D25). What that costs you is one rule:

> **Country- and customer-specific values live in `app_setting` or
> `config.py`, never as literals inside a service module.**

`PLAN.md` §15 inventories every Costa-Rica-specific assumption and prices
how expensive each is to change. Two are worth knowing before you write
code:

- **Phase 2's `/1.23`** (13% IVA + 10% servicio, servicio *outside* the IVA
  base) must land in `app_setting` as three fields — `iva_pct`,
  `servicio_pct`, `servicio_en_base_iva` — not as a constant. The third one
  is not obvious: this restaurant's own bills contradicted the textbook, so
  another restaurant's will too.
- **Spanish error text currently lives inside the pure services**
  (`units.py`, `graph.py` raise Spanish messages). That is deliberate — the
  market is Costa Rica and Latin America, and premature i18n would make
  those modules harder to read. It is also the one *sticky* assumption in
  the inventory. Do not add more of it than the errors already there.

**Deployment is one install per restaurant, single-tenant** (D24). There is
no `tenant_id` on any table and there must not be one — isolation is
physical. Do not add tenancy scaffolding "for later"; a SaaS pivot is a
rewrite of the data-access layer, and that trade was made knowingly.

**Multi-user is designed but unbuilt** (`PLAN.md` §16, D26-D29). Two rules
bind you now:

- **Auth ships in the same commit as the `0.0.0.0` bind, never after**
  (D28). The only thing protecting either app today is being unreachable
  from the network.
- **Never collapse "a human did this" into a boolean** (D29).
  `InsumoFuente.mapped_by` is a string for this reason; a nullable
  `confirmed_by_user_id` joins it later.

---

## Known debt

- **No Alembic** (D15). `create_all` creates missing tables and never
  alters an existing one, so a column added to `models.py` after its table
  exists needs a manual `ALTER TABLE`. Record every one of them here when
  it happens. This is inherited from upstream *along with its documented
  trap*, taken knowingly; wiring Alembic in both repos is a Phase 2
  candidate.
- **`/api/v1` upstream does not exist yet.** Everything downstream of it
  (`catalog_sync`, the mirror, every cost) is blocked on Phase 1 step 1.0,
  which is work in the *other* repo — including its decision-log entry and
  doc updates, which are not optional there.
- **`densidad_g_por_ml` ships unused** (D11), and cross-dimension
  conversion is refused until it is populated. Deliberate: the column
  exists so the refusal is a policy rather than a missing field.
- **`RecetaLinea.merma_pct_override` ships unused** (D7). Column now, UI
  when a real case shows up.
