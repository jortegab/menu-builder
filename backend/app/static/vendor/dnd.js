/* Drag-and-drop for the receta builder. Vendored, dependency-free, ~150
   lines of plain DOM -- no npm, no CDN, no build step, because this app
   runs on the restaurant's own machine and has to work with the internet
   down (PLAN.md §4).

   **It moves nothing on its own.** Every drop ends by submitting a form
   that already exists in the page and that a button already submits:
   dragging a palette item submits that item's own "Agregar" form, and
   dropping a reordered line submits the hidden `#form-orden`. So the drag
   path and the keyboard path go through exactly the same route, the same
   validation and the same server render -- drag is the fast way, never the
   only way, and never a second way with its own bugs (PLAN.md §10.1).

   Everything is delegated from `document` rather than bound to the rows,
   because htmx replaces the whole `#receta-cuerpo` on every line edit and
   listeners bound to the old nodes would quietly stop firing after the
   first drop. */
(function () {
  'use strict';

  // What is currently being dragged. A module variable rather than
  // dataTransfer, because dragover needs to know the *kind* of drag to
  // decide whether to accept, and dataTransfer's payload is deliberately
  // unreadable during dragover in most browsers.
  var arrastrando = null;

  function filas() {
    var cuerpo = document.getElementById('lineas-cuerpo');
    return cuerpo ? Array.prototype.slice.call(cuerpo.querySelectorAll('tr.linea')) : [];
  }

  function limpiar() {
    document.querySelectorAll('.is-destino').forEach(function (el) {
      el.classList.remove('is-destino');
    });
    document.querySelectorAll('.is-encima').forEach(function (el) {
      el.classList.remove('is-encima');
    });
  }

  document.addEventListener('dragstart', function (e) {
    var pal = e.target.closest ? e.target.closest('.pal[draggable]') : null;
    if (pal) {
      arrastrando = { tipo: 'paleta', el: pal };
      pal.classList.add('is-arrastrando');
    } else {
      var fila = e.target.closest ? e.target.closest('tr.linea') : null;
      if (!fila) return;
      arrastrando = { tipo: 'linea', el: fila };
      fila.classList.add('is-arrastrando');
    }
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      // Firefox refuses to start a drag unless something is set.
      try { e.dataTransfer.setData('text/plain', ''); } catch (err) { /* IE-era */ }
    }
  });

  document.addEventListener('dragend', function () {
    if (arrastrando) arrastrando.el.classList.remove('is-arrastrando');
    arrastrando = null;
    limpiar();
  });

  document.addEventListener('dragover', function (e) {
    if (!arrastrando) return;
    var soltar = e.target.closest ? e.target.closest('#soltar') : null;
    var fila = e.target.closest ? e.target.closest('tr.linea') : null;

    if (arrastrando.tipo === 'paleta') {
      // A palette item may land anywhere in the lines region -- the drop
      // zone or on top of an existing line. Order does not matter for an
      // add: it appends, and then it can be dragged.
      if (!soltar && !fila && !(e.target.closest && e.target.closest('#lineas'))) return;
      e.preventDefault();
      if (soltar) soltar.classList.add('is-encima');
      return;
    }

    // Reordering: the row under the cursor is where the dragged one goes.
    // The DOM is reordered live so the operator sees the result before
    // letting go; the server is not told until the drop.
    if (!fila || fila === arrastrando.el) return;
    e.preventDefault();
    limpiar();
    fila.classList.add('is-destino');
    var todas = filas();
    var origen = todas.indexOf(arrastrando.el);
    var destino = todas.indexOf(fila);
    if (origen < 0 || destino < 0) return;
    if (origen < destino) {
      fila.parentNode.insertBefore(arrastrando.el, fila.nextSibling);
    } else {
      fila.parentNode.insertBefore(arrastrando.el, fila);
    }
  });

  document.addEventListener('dragleave', function (e) {
    var soltar = e.target.closest ? e.target.closest('#soltar') : null;
    if (soltar) soltar.classList.remove('is-encima');
  });

  document.addEventListener('drop', function (e) {
    if (!arrastrando) return;
    var dentro =
      e.target.closest &&
      (e.target.closest('#soltar') ||
        e.target.closest('#lineas') ||
        e.target.closest('.builder__cuerpo'));
    if (!dentro) return;
    e.preventDefault();

    if (arrastrando.tipo === 'paleta') {
      enviarAgregar(arrastrando.el);
    } else {
      enviarOrden();
    }
    limpiar();
  });

  /* Submit the palette item's own form -- the same one its "Agregar"
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
     from the DOM as it now looks, which is what the operator just arranged
     -- the server is the one that decides whether it is legal, and it
     answers with the re-costed body. */
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
})();
