# `dnd.js` — la lista que hay que correr a mano

`backend/app/static/vendor/dnd.js` has **no automated test, deliberately.**
Testing it needs a DOM, a DOM needs npm, and npm is what D2 rules out. That
trade stands. What does not stand is having no written record of what
"working" means — so this is that record (DESIGN.md §4.4).

**Run it after any change to `dnd.js`, `_cuerpo.html` or `_paleta.html`,
on a mouse *and* on a real tablet.** A simulated touch in devtools does
not exercise the same code: it reports `pointerType: "touch"` but never
reproduces the browser's own scroll arbitration, which is exactly what the
long-press exists to negotiate.

Setup: `docker compose up -d --build web`, then open a receta with at least
three lines, one of them a preparación (the live fixture *Pollo a la
plancha* has this). `http://localhost:8001/recetas/2`.

---

## 0. The rule everything else protects

`dnd.js` **invents nothing.** Every drop ends by submitting a form that
already exists in the page and that a button already submits. If any check
below passes because JavaScript did something the server did not, that is
the bug — not a nicety.

- [ ] **Delete the `<script src=".../dnd.js">` line from `base.html` and
      reload.** Every check in §4 (the keyboard path) still passes, and the
      builder is fully usable: «Agregar», ↑, ↓, the trash button and the
      quantity stepper all work. Put the line back.

## 1. Mouse — the palette

- [ ] Press on a palette row's **grip** (the six-dot handle at its left)
      and move. A floating chip with the item's name follows the cursor;
      the row itself fades to ~45%.
- [ ] Pressing on the row's **name** and moving selects text or does
      nothing. It does **not** start a drag — only the grip lifts.
- [ ] Dragging over the plate lights the dashed «Soltá acá» zone teal.
- [ ] Releasing over the plate adds the line, and the totals below
      recompute. The line lands at the **end** of the list.
- [ ] Releasing over the rail, the page header, or outside the window adds
      **nothing** and leaves the plate untouched.
- [ ] `Esc` mid-drag cancels: chip gone, nothing added.

## 2. Mouse — reordering lines

- [ ] Press a line's grip and move. The chip shows that ingredient's name.
- [ ] Moving over another row rearranges the list **live**, and the row
      being passed over shows a teal rule at its top edge.
- [ ] **A preparación's «de qué está hecho el lote» block travels with its
      own row.** Drag the preparación line past two others: its breakdown
      stays attached underneath it and never detaches onto a different
      ingredient. *(This is the check that regressed silently under HTML5
      drag — the breakdown is a DOM sibling, not a child.)*
- [ ] Releasing over the plate submits the new order and the body
      re-renders with the same order the screen showed.
- [ ] Releasing **outside** the plate puts the row back where it started.
      Nothing is submitted; reload and the original order is intact.
- [ ] `Esc` mid-drag does the same — row back, nothing submitted.
- [ ] Clicking a grip **without moving** does not open the inspector.
      Clicking anywhere else on the row does.

## 3. Touch — a real tablet, not devtools

- [ ] **Scroll the palette with a finger anywhere on a row except the
      grip.** It scrolls normally. *(This is what the HTML5 version broke:
      `touch-action:none` on the whole row.)*
- [ ] **Flick from directly on a grip.** Nothing lifts and nothing
      reorders. The list may not scroll from that 44px strip — that is the
      cost of the grip owning the gesture, and it is why the grip is
      narrow and everything around it scrolls.
- [ ] **Hold a grip still for about a third of a second.** The item lifts:
      chip appears, row fades.
- [ ] Once lifted, dragging works exactly as §1 and §2 describe.
- [ ] Lifting a line and dragging it near the **top or bottom edge of the
      screen** scrolls the page, and the drop indicator keeps updating
      while it scrolls.
- [ ] Lifting a palette item, dragging to the plate — which on a tablet is
      often not on screen with the palette — and releasing adds the line.
- [ ] Every grip is at least a fingertip wide (44px on a coarse pointer).
- [ ] Tapping the amber **«viejo»** pip on `/insumos` shows its
      explanation. Tapping a **composition-bar segment** shows that
      ingredient's name and cost. *(Neither was reachable at all before —
      both lived in `title=`.)*

## 4. Keyboard — the path that must never depend on any of the above

- [ ] `Tab` reaches every row's ↑, ↓ and trash buttons.
- [ ] ↑ and ↓ reorder, and the first/last row's respective button is
      disabled.
- [ ] The quantity input takes a comma (`12,5`) and keeps focus while a
      three-digit number is typed.
- [ ] `Enter` in the quantity input **applies the typed value** — it does
      not step it down. *(The «Aplicar» button is deliberately first in the
      DOM for this; a form submitted with Enter uses its first submit
      button.)*

## 5. Widths

- [ ] **≥1280px** — palette, plate, and the inspector as a third column
      when a line is selected.
- [ ] **1100–1279px** — the rail is icons only, the mapeo badge is a dot,
      and the inspector slides in over the palette.
- [ ] **821–1099px** — same, still two columns.
- [ ] **≤820px** — the rail is a horizontal strip across the top and
      everything stacks, palette above the lines.

---

## If something here fails

Fix `dnd.js`, not the server. The routes are shared with the keyboard path
and with the no-JavaScript path, and `tests/test_routes_smoke.py` covers
both shapes of every one of them. A drag that needs a route change is a
drag that has stopped being the fast path and started being a second
implementation.
