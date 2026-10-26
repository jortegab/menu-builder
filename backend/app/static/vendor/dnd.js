/* Pointer behaviour for the receta builder: dragging, and selecting a row.
   Vendored, dependency-free plain DOM -- no npm, no CDN, no build step,
   because this app runs on the restaurant's own machine and has to work
   with the internet down (PLAN.md §4).

   **Pointer Events, not HTML5 drag** (DESIGN.md D34). `dragstart` and
   friends do not fire on a touchscreen at all, so until this rewrite the
   builder's headline interaction simply did not exist on the tablet it is
   meant for -- and nobody noticed, because the ↑ ↓ keyboard path still
   worked. `pointerdown`/`pointermove`/`pointerup` with `setPointerCapture`
   is ONE code path for mouse, pen and finger; there is deliberately no
   touch branch bolted onto a mouse implementation, which is how this
   usually goes wrong.

   **It invents nothing on its own.** Every drop ends by submitting a form
   that already exists in the page and that a button already submits, and
   selecting a row follows a link that is already inside that row:
   dragging a palette item submits that item's own «Agregar» form, and
   dropping a reordered line submits the hidden `#form-orden`. So the drag
   path and the keyboard path go through exactly the same route, the same
   validation and the same server render -- drag is the fast way, never the
   only way, and never a second way with its own bugs (PLAN.md §10.1).
   Delete this file and the builder still works.

   Everything is delegated from `document` rather than bound to the rows,
   because htmx replaces the whole `#receta-cuerpo` on every line edit and
   listeners bound to the old nodes would quietly stop firing after the
   first drop.

   No automated test, deliberately: testing it needs a DOM, a DOM needs
   npm, and npm is what D2 rules out. `tests/manual/dnd.md` is the written
   record of what "working" means -- run it by hand, on a mouse AND on a
   real tablet, after any change to this file. */
(function () {
  'use strict';

  /* A mouse lifts as soon as it has clearly moved; a finger has to hold
     still first. The hold is what keeps a scroll from being read as a
     drag: the grips are the only `touch-action:none` surfaces in the
     builder, and everything around them scrolls normally, so a finger
     that lands on a grip and immediately travels is far more likely to be
     someone scrolling the list than someone reordering it. */
  var UMBRAL_RATON = 5;    // px of travel before a mouse drag is a drag
  var UMBRAL_TOQUE = 10;   // px of travel that abandons a pending lift
  var ESPERA_TOQUE = 350;  // ms a finger must hold still to lift
  var BORDE = 76;          // px from a viewport edge that auto-scrolls
  var VELOCIDAD = 15;      // px per frame at the very edge

  var pendiente = null;  // a pointer is down on a grip, nothing lifted yet
  var arrastre = null;   // something is lifted and following the pointer
  var arrastro = false;  // the gesture that just ended was a drag, not a click
  var ultimo = { x: 0, y: 0 };
  var velocidad = 0;
  var cuadro = 0;

  function filas() {
    var cuerpo = document.getElementById('lineas-cuerpo');
    return cuerpo ? Array.prototype.slice.call(cuerpo.querySelectorAll('.linea')) : [];
  }

  /* A line and its own nested breakdown are siblings in the DOM, so moving
     the row alone leaves the «de qué está hecho el lote» block behind,
     attached to whatever row now sits above it. Three-level nests are the
     real case here (fondo oscuro → salsa criolla → plato), so the pair
     moves together or the drag lies about what it is doing. */
  function grupo(fila) {
    var g = [fila];
    var sig = fila.nextElementSibling;
    if (sig && sig.classList.contains('desglose')) g.push(sig);
    return g;
  }

  function insertarAntes(fila, ref) {
    var g = grupo(fila);
    var padre = fila.parentNode;
    if (!padre) return;
    for (var i = 0; i < g.length; i++) padre.insertBefore(g[i], ref);
  }

  function despuesDe(fila) {
    var g = grupo(fila);
    return g[g.length - 1].nextSibling;
  }

  function limpiar() {
    var marcadas = document.querySelectorAll('.is-destino, .is-encima');
    for (var i = 0; i < marcadas.length; i++) {
      marcadas[i].classList.remove('is-destino');
      marcadas[i].classList.remove('is-encima');
    }
  }

  function nombreDe(el, tipo) {
    var n = tipo === 'paleta'
      ? el.querySelector('.pal__nombre')
      : el.querySelector('.linea__nom a');
    return n ? n.textContent.trim() : '';
  }

  /* What the pointer carries. HTML5 drag drew this for free; on a
     touchscreen there is no cursor at all, so without it a lifted item is
     invisible and the gesture has no feedback until the drop. It has
     `pointer-events:none` -- otherwise `elementFromPoint` would hit the
     ghost instead of whatever is under the finger, and nothing would ever
     be a valid drop target. */
  function crearFantasma(el, tipo) {
    var d = document.createElement('div');
    d.className = 'arrastre';
    d.textContent = nombreDe(el, tipo);
    document.body.appendChild(d);
    return d;
  }

  // ── el gesto ───────────────────────────────────────────────────────────

  document.addEventListener('pointerdown', function (e) {
    if (e.button) return;                       // primary button / any touch
    if (arrastre) return;                       // a second finger is not a second drag
    if (!e.target.closest) return;
    // A grip is the only place a drag can start. The rest of a row is a
    // click target (select the line) and holds real controls.
    var asa = e.target.closest('.linea__asa, .pal__asa');
    if (!asa) return;
    var el = asa.closest('.linea') || asa.closest('.pal');
    if (!el) return;

    cancelarPendiente();
    pendiente = {
      tipo: el.classList.contains('pal') ? 'paleta' : 'linea',
      el: el,
      x: e.clientX,
      y: e.clientY,
      toque: e.pointerType === 'touch',
      captor: null,
      temporizador: 0
    };
    /* Capture on the grip so moves keep arriving once the pointer leaves
       it -- which it does immediately, since the whole point is to travel
       somewhere else. Capture redirects events; it does not affect
       hit-testing, so `elementFromPoint` still sees what is underneath. */
    try {
      asa.setPointerCapture(e.pointerId);
      pendiente.captor = asa;
      pendiente.pointerId = e.pointerId;
    } catch (err) { /* older engines: the document listeners still fire */ }

    if (pendiente.toque) {
      pendiente.temporizador = setTimeout(function () {
        if (pendiente) iniciar();
      }, ESPERA_TOQUE);
    }
  });

  document.addEventListener('pointermove', function (e) {
    if (pendiente && !arrastre) {
      var dx = Math.abs(e.clientX - pendiente.x);
      var dy = Math.abs(e.clientY - pendiente.y);
      if (pendiente.toque) {
        // Travelled before the hold elapsed: that was a scroll attempt or
        // a slip, not a lift. Drop it and do nothing at all.
        if (dx > UMBRAL_TOQUE || dy > UMBRAL_TOQUE) cancelarPendiente();
        return;
      }
      if (dx < UMBRAL_RATON && dy < UMBRAL_RATON) return;
      iniciar();
    }
    if (!arrastre) return;
    e.preventDefault();
    mover(e.clientX, e.clientY);
  }, { passive: false });

  document.addEventListener('pointerup', function (e) {
    if (!arrastre) { cancelarPendiente(); return; }
    soltar(e.clientX, e.clientY);
  });

  document.addEventListener('pointercancel', function () { cancelar(); });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && arrastre) cancelar();
  });

  function cancelarPendiente() {
    if (!pendiente) return;
    clearTimeout(pendiente.temporizador);
    liberar(pendiente);
    pendiente = null;
  }

  function liberar(p) {
    if (!p || !p.captor || p.pointerId === undefined) return;
    try { p.captor.releasePointerCapture(p.pointerId); } catch (err) { /* already gone */ }
  }

  function iniciar() {
    var p = pendiente;
    clearTimeout(p.temporizador);
    arrastre = {
      tipo: p.tipo,
      el: p.el,
      // Where it came from, so Escape or a drop in the void puts it back.
      ancla: p.tipo === 'linea' ? despuesDe(p.el) : null,
      fantasma: crearFantasma(p.el, p.tipo),
      captura: p
    };
    arrastro = true;
    p.el.classList.add('is-arrastrando');
    document.documentElement.classList.add('arrastrando');
    mover(p.x, p.y);
  }

  function mover(x, y) {
    ultimo.x = x;
    ultimo.y = y;
    if (arrastre.fantasma) {
      arrastre.fantasma.style.transform = 'translate(' + x + 'px,' + y + 'px)';
    }
    evaluar();
    autodesplazar(y);
  }

  /* What is under the pointer right now, and what that means. Split from
     `mover` so the auto-scroll loop can re-run it while the pointer sits
     still at the edge of the screen -- otherwise the drop target would
     freeze the moment the page started scrolling under it. */
  function evaluar() {
    var bajo = document.elementFromPoint(ultimo.x, ultimo.y);
    limpiar();
    if (!bajo || !bajo.closest) return;

    if (arrastre.tipo === 'paleta') {
      // A palette item may land anywhere in the plate: an add appends, so
      // position does not matter and can be fixed by dragging afterwards.
      if (!zonaDeSuelta(bajo)) return;
      var s = document.getElementById('soltar');
      if (s) s.classList.add('is-encima');
      return;
    }

    // Reordering: the row under the pointer is where the lifted one goes.
    // The DOM is rearranged live so the operator sees the result before
    // letting go; the server is not told until the drop.
    var fila = bajo.closest('.linea');
    if (!fila || fila === arrastre.el) return;
    fila.classList.add('is-destino');
    var todas = filas();
    var origen = todas.indexOf(arrastre.el);
    var destino = todas.indexOf(fila);
    if (origen < 0 || destino < 0) return;
    insertarAntes(arrastre.el, origen < destino ? despuesDe(fila) : fila);
  }

  function zonaDeSuelta(bajo) {
    return bajo.closest('#soltar') || bajo.closest('#lineas') || bajo.closest('.builder__plato');
  }

  /* On a tablet the palette and the drop target are frequently not on
     screen together, so a lifted item near an edge pulls the page. */
  function autodesplazar(y) {
    var alto = window.innerHeight;
    if (y < BORDE) velocidad = -Math.ceil((BORDE - y) / BORDE * VELOCIDAD);
    else if (y > alto - BORDE) velocidad = Math.ceil((y - (alto - BORDE)) / BORDE * VELOCIDAD);
    else velocidad = 0;
    if (velocidad && !cuadro) cuadro = requestAnimationFrame(paso);
  }

  function paso() {
    cuadro = 0;
    if (!arrastre || !velocidad) return;
    window.scrollBy(0, velocidad);
    evaluar();
    cuadro = requestAnimationFrame(paso);
  }

  function terminar(a) {
    if (cuadro) cancelAnimationFrame(cuadro);
    cuadro = 0;
    velocidad = 0;
    if (a) {
      a.el.classList.remove('is-arrastrando');
      if (a.fantasma && a.fantasma.parentNode) {
        a.fantasma.parentNode.removeChild(a.fantasma);
      }
      liberar(a.captura);
    }
    document.documentElement.classList.remove('arrastrando');
    limpiar();
    arrastre = null;
    pendiente = null;
  }

  function cancelar() {
    if (!arrastre) { cancelarPendiente(); return; }
    var a = arrastre;
    // Put a reordered line back where it was: the live rearrangement was
    // a preview, and a cancelled preview must not survive as a silent
    // edit that the next drop would then submit as if it were intended.
    if (a.tipo === 'linea') insertarAntes(a.el, a.ancla);
    terminar(a);
  }

  function soltar(x, y) {
    ultimo.x = x;
    ultimo.y = y;
    var bajo = document.elementFromPoint(x, y);
    var dentro = bajo && bajo.closest && zonaDeSuelta(bajo);
    var a = arrastre;
    // Dropped in the void: a palette item simply never lands, and a line
    // goes back where it started, because everything up to here was a
    // preview of a drop that did not happen.
    if (!dentro && a.tipo === 'linea') insertarAntes(a.el, a.ancla);
    terminar(a);
    if (!dentro) return;
    if (a.tipo === 'paleta') enviarAgregar(a.el);
    else enviarOrden();
  }

  // ── lo único que esto hace: mandar un form que ya existía ──────────────

  /* Submit the palette item's own form -- the same one its «Agregar»
     button submits. `requestSubmit` fires a real submit event, which is
     what htmx listens for; `submit()` would bypass htmx and navigate. */
  function enviarAgregar(form) {
    if (form.requestSubmit) {
      form.requestSubmit();
    } else {
      var boton = form.querySelector('button[type=submit]');
      if (boton) boton.click();
    }
  }

  /* Write the new order into the hidden form and submit it. The ids come
     from the DOM as it now looks, which is what the operator just
     arranged -- the server is the one that decides whether it is legal,
     and it answers with the re-costed body. */
  function enviarOrden() {
    var form = document.getElementById('form-orden');
    if (!form) return;
    var campo = form.querySelector('[name=orden]');
    if (!campo) return;
    campo.value = filas()
      .map(function (fila) { return fila.getAttribute('data-linea-id'); })
      .filter(Boolean)
      .join(',');
    enviarAgregar(form);
  }

  /* Clicking anywhere on a row opens its «¿Por qué este costo?» pane.

     This adds no route and no state. The row already contains an <a> to
     `?linea=<id>` -- that link is the no-JavaScript path and stays the
     only thing that actually navigates. All this does is widen the target
     from the ingredient's name to the whole row, because on a tablet a
     text link is a small thing to hit and the row is the object the
     operator is thinking about.

     Anything interactive is left alone: a click on the stepper, the unit
     select, a delete button or the preparación link means that, and not
     "select this row". So is the grip -- the click that ends a drag lands
     on it, and selecting a row because it was just reordered would be a
     navigation nobody asked for. */
  document.addEventListener('click', function (e) {
    if (arrastro) { arrastro = false; return; }
    if (!e.target.closest) return;
    if (e.target.closest('a, button, input, select, textarea, label')) return;
    if (e.target.closest('.linea__asa, .pal__asa')) return;
    var fila = e.target.closest('.linea');
    if (!fila) return;
    var enlace = fila.querySelector('.linea__nom a[href*="?linea="]');
    if (enlace) enlace.click();
  });
})();
