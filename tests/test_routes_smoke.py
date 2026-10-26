"""Every screen renders, against a database with real-ish rows in it.

Ported from restaurant-manager's own smoke test, and for the reason that
one exists: unit tests structurally cannot catch a template referencing a
context key the router stopped passing, a Jinja filter that moved, or a
router importing a helper that was extracted to a service. Every one of
those is a 500 on a page the operator uses daily.

The builder is the app (PLAN.md principle 7), so it gets more than a GET
here: adding, editing, reordering and deleting a line all go through the
real routes, in both their htmx and their plain-form shapes, because
JavaScript switched off has to work too.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from unittest import mock
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

try:
    from fastapi.testclient import TestClient

    HAVE_CLIENT = True
except ImportError:  # pragma: no cover - httpx missing
    HAVE_CLIENT = False

from sqlalchemy import func, select  # noqa: E402

from app.models import (  # noqa: E402
    CatalogDescarte,
    CatalogPricePoint,
    CatalogProduct,
    Insumo,
    InsumoFuente,
    RecetaLinea,
)
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import mapeo as mapeo_svc  # noqa: E402
from app.services import recetas as recetas_svc  # noqa: E402

HOY = dt.date(2026, 9, 2)


@unittest.skipUnless(HAVE_CLIENT, "fastapi.testclient needs httpx installed")
class RouteSmokeTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.db import get_db
        from app.main import app

        # `init_db` is deliberately not run: DbTestCase already created the
        # schema, and letting lifespan touch the configured Postgres URL
        # would make this test need a server.
        app.dependency_overrides[get_db] = self.override_get_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self._seed()

    def _seed(self) -> None:
        self.pechuga = insumos_svc.create(
            self.db, nombre="Pechuga deshuesada", unidad_base="g", merma_pct="12"
        )
        self.sal = insumos_svc.create(self.db, nombre="Sal", unidad_base="g")

        producto = CatalogProduct(
            internal_code="11-0001", upstream_product_id=1, supplier_id=11,
            supplier_nombre="Inversiones Hevar", description="PECHUGA DESHUESADA 1/5KG",
            categoria_slug="alimentos_carnes",
        )
        self.db.add(producto)
        self.db.flush()
        self.db.add(
            CatalogPricePoint(
                catalog_product_id=producto.id, clave="506" + "1" * 47, numero_linea=1,
                fecha=HOY - dt.timedelta(days=5), cantidad=Decimal(2),
                precio_efectivo=Decimal("20000"), precio_efectivo_crc=Decimal("20000"),
                precio_efectivo_normalizado_crc=Decimal("20000"),
            )
        )
        self.db.add(
            InsumoFuente(
                insumo_id=self.pechuga.id, catalog_product_id=producto.id,
                factor_conversion=Decimal(5000), factor_origen="manual",
                status="confirmed", mapped_by="human", activo=True,
            )
        )
        self.db.commit()

        self.salsa = recetas_svc.create(
            self.db, nombre="Salsa criolla", tipo="preparacion",
            rendimiento_cantidad="2500", rendimiento_unidad="g",
        )
        recetas_svc.agregar_linea(
            self.db, self.salsa, tipo_componente="insumo",
            componente_id=self.sal.id, cantidad="10",
        )
        self.plato = recetas_svc.create(
            self.db, nombre="Pollo a la plancha", tipo="plato", porciones="1",
            categoria_menu="Fondos",
        )
        recetas_svc.agregar_linea(
            self.db, self.plato, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="180",
        )
        recetas_svc.agregar_linea(
            self.db, self.plato, tipo_componente="receta",
            componente_id=self.salsa.id, cantidad="45", unidad="g",
        )

    # -- GETs ------------------------------------------------------------

    def test_every_page_renders(self):
        paginas = [
            "/salud",
            "/recetas",
            "/recetas?vista=tabla",
            "/recetas/nueva",
            "/recetas/nueva?tipo=preparacion",
            "/preparaciones",
            "/insumos",
            "/insumos/nuevo",
            "/insumos/mapeo",
            "/insumos/mapeo?desde=1",
            "/insumos/mapeo?cat=alimentos_verduras&cat=alimentos_frutas",
            "/ajustes",
            f"/insumos/{self.pechuga.id}",
            f"/insumos/{self.pechuga.id}/editar",
            f"/recetas/{self.plato.id}",
            f"/recetas/{self.plato.id}/editar",
            f"/recetas/{self.salsa.id}",
            f"/recetas/{self.plato.id}/paleta",
            f"/recetas/{self.plato.id}/paleta?q=pech",
            f"/recetas/{self.plato.id}/cocina",
            f"/recetas/{self.salsa.id}/cocina",
            f"/recetas/{self.plato.id}/simular",
            f"/recetas/{self.salsa.id}/simular",
            "/buscar",
            "/buscar?q=pech",
            "/buscar?q=zzznada",
        ]
        for url in paginas:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, r.text[:400])

    def test_nueva_no_es_capturada_por_el_id(self):
        """`/recetas/{id}` would match "nueva" and 422 on the int
        conversion. Route order is the fix, and this is what would notice
        if someone reordered the file."""
        r = self.client.get("/recetas/nueva")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nuevo plato", r.text)

    def test_una_receta_incompleta_no_muestra_un_total_parcial(self):
        """D10 on the actual screen: Sal has no price, so the plato has a
        priced pechuga line and no total at all."""
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertIn("Receta incompleta", r.text)
        self.assertIn("«Sal»", r.text)
        self.assertIn("—", r.text)

    def test_los_faltantes_enlazan_al_insumo(self):
        """Naming the ingredient is half of D10; the next click has to be
        its own screen, or the operator has to go find it by hand."""
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertIn(f'<a href="/insumos/{self.sal.id}">«Sal»</a>', r.text)

    def test_el_builder_carga_htmx_y_el_modulo_de_arrastre(self):
        """Both vendored, never from a CDN: this app has to work with the
        internet down (D2)."""
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertIn("/static/vendor/htmx.min.js", r.text)
        self.assertIn("/static/vendor/dnd.js", r.text)
        self.assertNotIn("//cdn", r.text)
        self.assertNotIn("unpkg", r.text)

    def test_el_arrastre_va_por_asas_y_no_por_html5(self):
        """D34: `draggable` + `dragstart` do not fire on a touchscreen, so
        the builder had no drag at all on the tablet it is built for.

        A template cannot be unit-tested and `dnd.js` cannot be tested at
        all (D2 rules out the DOM it would need), so this is the only thing
        standing between a stray `draggable="true"` reappearing and the
        tablet silently losing drag again. The grip is the other half: it
        is the one surface that owns the touch gesture, and without it in
        the markup nothing can be lifted by a finger."""
        for url in (f"/recetas/{self.plato.id}", f"/recetas/{self.plato.id}/paleta"):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertNotIn("draggable", r.text)
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertIn('class="linea__asa"', r.text)
        self.assertIn('class="pal__asa"', r.text)

    def test_ningun_dato_vive_solo_en_un_title(self):
        """§4.2: `title=` is hover-only, and a tablet has no hover. A title
        that carries a *label* for a button that already has an aria-label
        is fine; one that carries a figure is information the operator on
        the pass can never reach, so those are `.tip[data-tooltip]`."""
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertIn('class="comp__seg tip"', r.text)
        self.assertIn("data-tooltip=", r.text)

    def test_toda_linea_tiene_camino_de_teclado(self):
        """Drag is the fast path, never the only one (PLAN.md §10.1). If a
        row ever loses its arrows this fails before a keyboard user does."""
        r = self.client.get(f"/recetas/{self.plato.id}")
        for etiqueta in ("Subir", "Bajar", "Quitar"):
            self.assertIn(f'aria-label="{etiqueta} Pechuga deshuesada"', r.text)
        self.assertIn(">Agregar<", r.text)

    def test_la_paleta_responde_sola_a_la_busqueda(self):
        r = self.client.get(f"/recetas/{self.plato.id}/paleta?q=sal")
        self.assertNotIn("<html", r.text)
        self.assertIn("Sal", r.text)
        self.assertNotIn("Pechuga deshuesada", r.text)

    def test_el_selector_de_unidad_solo_ofrece_la_misma_dimension(self):
        """An incompatible pair has to be unreachable through the UI, not
        merely rejected once it has been chosen (D30)."""
        r = self.client.get(f"/recetas/{self.plato.id}")
        # The pechuga line is costed in g; its selector must not offer ml.
        cuerpo = r.text
        self.assertIn('<option value="kg"', cuerpo)
        self.assertNotIn('<option value="ml"', cuerpo)

    # -- the builder's writes ---------------------------------------------

    def test_agregar_una_linea_por_htmx_devuelve_el_cuerpo_recosteado(self):
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas",
            data={"tipo_componente": "insumo", "componente_id": self.sal.id, "cantidad": "2"},
            headers={"hx-request": "true"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="receta-cuerpo"', r.text)
        self.assertNotIn("<html", r.text)

    def test_agregar_una_linea_sin_htmx_redirige_a_la_receta(self):
        """JavaScript off has to work: htmx is the fast path, never the
        only one."""
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas",
            data={"tipo_componente": "insumo", "componente_id": self.sal.id, "cantidad": "2"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], f"/recetas/{self.plato.id}")

    def test_una_linea_invalida_reporta_el_error_sin_perder_la_pantalla(self):
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas",
            data={
                "tipo_componente": "insumo", "componente_id": self.pechuga.id,
                "cantidad": "6", "unidad": "ml",
            },
            headers={"hx-request": "true"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("magnitudes distintas", r.text)
        # And the rest of the recipe is still on screen.
        self.assertIn("Pechuga deshuesada", r.text)

    def test_editar_la_cantidad_mueve_el_total(self):
        linea = self.plato.lineas[0]
        antes = self.client.get(f"/recetas/{self.plato.id}").text
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas/{linea.id}",
            data={"cantidad": "360", "unidad": "g"},
            headers={"hx-request": "true"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(antes, r.text)

    def test_reordenar_por_ids(self):
        ids = [ln.id for ln in self.plato.lineas]
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas/orden",
            data={"orden": ",".join(str(i) for i in reversed(ids))},
            headers={"hx-request": "true"},
        )
        self.assertEqual(r.status_code, 200)
        self.db.expire_all()
        nuevo = sorted(self.plato.lineas, key=lambda ln: ln.orden)
        self.assertEqual([ln.id for ln in nuevo], list(reversed(ids)))

    def test_orden_no_es_capturada_por_el_id_de_linea(self):
        """`/lineas/{linea_id}` would match "orden" and 422. Declaration
        order is the fix, same trap as `/recetas/nueva`."""
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas/orden",
            data={"orden": ""},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)

    def test_mover_y_eliminar(self):
        ids = [ln.id for ln in self.plato.lineas]
        self.client.post(
            f"/recetas/{self.plato.id}/lineas/{ids[0]}/mover",
            data={"delta": "1"}, headers={"hx-request": "true"},
        )
        r = self.client.post(
            f"/recetas/{self.plato.id}/lineas/{ids[0]}/eliminar",
            headers={"hx-request": "true"},
        )
        self.assertEqual(r.status_code, 200)
        self.db.expire_all()
        self.assertEqual([ln.id for ln in self.plato.lineas], [ids[1]])

    def test_una_linea_de_otra_receta_no_se_puede_tocar_desde_aca(self):
        ajena = self.salsa.lineas[0]
        self.client.post(
            f"/recetas/{self.plato.id}/lineas/{ajena.id}/eliminar",
            headers={"hx-request": "true"},
        )
        self.db.expire_all()
        self.assertEqual(len(self.salsa.lineas), 1)

    # -- histórico ---------------------------------------------------------

    def test_la_tarjeta_de_historico_no_miente_estando_vacia(self):
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertIn("Histórico de costo", r.text)
        self.assertIn("Todavía no hay costos guardados", r.text)
        # The chart's own class, not "<svg". Those were the same assertion
        # until the shell landed its icon set (DESIGN.md §2 A3) and every
        # page grew a dozen inline SVGs in the rail alone. What this test
        # is actually about is that an empty histórico draws no CURVE.
        self.assertNotIn("cost-chart", r.text)

    def test_guardar_hoy_y_sembrar_dibujan_la_curva(self):
        # A receta whose every ingredient has a price -- the seeded plato is
        # deliberately incomplete, and an incomplete day stores no number to
        # plot, which is D10 all the way into the chart.
        completa = recetas_svc.create(
            self.db, nombre="Pechuga sola", tipo="plato", porciones="1"
        )
        recetas_svc.agregar_linea(
            self.db, completa, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="180",
        )
        self.client.post("/ajustes/historico?backfill=1", follow_redirects=False)
        self.client.post("/ajustes/historico", follow_redirects=False)
        r = self.client.get(f"/recetas/{completa.id}")
        self.assertIn("cost-chart", r.text)
        self.assertIn("Variación", r.text)
        self.assertIn("Mediana", r.text)
        # The seeded stretch has to be legible as seeded.
        self.assertIn("es-estimado", r.text)
        self.assertIn("estimados", r.text)

    def test_el_panel_de_ajustes_cuenta_lo_guardado(self):
        self.client.post("/ajustes/historico", follow_redirects=False)
        r = self.client.get("/ajustes")
        self.assertIn("Costos guardados", r.text)
        self.assertIn("Días con datos", r.text)

    def test_crear_una_receta_desde_el_formulario(self):
        r = self.client.post(
            "/recetas",
            data={"nombre": "Arroz con pollo", "tipo": "plato", "porciones": "4"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/recetas/"))

    def test_un_formulario_invalido_vuelve_con_lo_que_se_escribio(self):
        r = self.client.post(
            "/recetas", data={"nombre": "Sin porciones", "tipo": "plato", "porciones": ""}
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Sin porciones", r.text)
        self.assertIn("porciones", r.text)


    # -- inspector (DESIGN.md §3.1) --------------------------------------
    # Which line the pane shows rides in the query string rather than in
    # JS state, which is what makes it bookmarkable and survivable with
    # JavaScript off -- and is also what makes these cases reachable.

    def _linea_de_insumo(self):
        return next(
            ln for ln in self.plato.lineas if ln.tipo_componente == "insumo"
        )

    def test_seleccionar_una_linea_abre_el_panel(self):
        ln = self._linea_de_insumo()
        r = self.client.get(f"/recetas/{self.plato.id}?linea={ln.id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Línea seleccionada", r.text)
        self.assertIn("builder--insp", r.text)
        self.assertIn("is-sel", r.text)

    def test_sin_linea_no_hay_tercera_columna(self):
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertNotIn("builder--insp", r.text)
        self.assertNotIn("Línea seleccionada", r.text)

    def test_una_linea_borrada_no_revienta_la_pagina(self):
        # The id is in the URL, so a reloaded or bookmarked builder can name
        # a line that no longer exists. That must render the page without
        # the pane, never 500.
        r = self.client.get(f"/recetas/{self.plato.id}?linea=999999")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("builder--insp", r.text)

    def test_una_preparacion_no_abre_panel_de_insumo(self):
        ln = next(
            ln for ln in self.plato.lineas if ln.tipo_componente == "receta"
        )
        r = self.client.get(f"/recetas/{self.plato.id}?linea={ln.id}")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("builder--insp", r.text)

    # -- composición (D39) -------------------------------------------------

    def test_no_queda_ninguna_columna_de_porcentaje(self):
        r = self.client.get(f"/recetas/{self.plato.id}")
        self.assertNotIn(">%</", r.text)

    def test_una_receta_incompleta_no_llama_a_esto_la_composicion_del_plato(self):
        # The seeded plato is incomplete, so any percentage here is a share
        # of a PARTIAL sum -- the same claim D10 refuses to make with the
        # total, which is rendered as an em dash right below it.
        r = self.client.get(f"/recetas/{self.plato.id}")
        if "comp__bar" in r.text:
            self.assertIn("comp__t--parcial", r.text)
            self.assertIn("no es", r.text)


    # -- el tablero y la triage (paso 2.3) ---------------------------------

    def test_el_menu_abre_en_tablero_y_la_tabla_queda_a_un_clic(self):
        """D38: a menu has categorías and dishes people picture, and the
        board is that object. The table stays one click away for the "rank
        every plate by cost" question, which the board is bad at."""
        r = self.client.get("/recetas")
        self.assertIn('class="tablero"', r.text)
        self.assertIn("Fondos", r.text)          # su categoría, como grupo
        self.assertNotIn("<thead>", r.text)
        tabla = self.client.get("/recetas?vista=tabla")
        self.assertIn("<thead>", tabla.text)
        self.assertNotIn('class="tablero"', tabla.text)

    def test_las_preparaciones_no_tienen_tablero(self):
        """A preparación is an ingredient, not a menu item, and «usada en
        N» is a column, not a card. The toggle is not offered, and forcing
        it in the query string still gives the table."""
        r = self.client.get("/preparaciones?vista=tablero")
        self.assertIn("<thead>", r.text)
        self.assertNotIn('class="tablero"', r.text)

    def test_una_receta_incompleta_no_muestra_costo_en_la_tarjeta(self):
        """D10 on the board: the plato's Sal has no price, so its card
        shows an em dash and never a partial sum dressed as a cost."""
        r = self.client.get("/recetas")
        self.assertIn("plato__costo--nulo", r.text)

    def test_el_mapeo_muestra_un_producto_a_la_vez(self):
        """A page that emits one card per pending product gets longer the
        more work you have, which is backwards -- and with a key configured
        it cost one billed LLM call per card rendered."""
        self._producto_pendiente()
        r = self.client.get("/insumos/mapeo")
        self.assertEqual(r.text.count('name="catalog_product_id"'), 1)

    def test_la_barra_de_filtros_no_es_una_tarjeta_ni_lleva_porton(self):
        """What replaces `test_el_porton_nombra_su_propio_tamano`.

        That test asserted the bulk gate named its own size («descartar
        403»), which mattered while the gate was the answer to a catalog
        full of cloro and servilletas. The scope setting keeps those out by
        family now, so the gate came off the screen on 2026-09-07 along
        with the card around the search box.

        The route is still there and still tested below -- what is asserted
        here is that nothing on this screen reaches it, because a bulk
        dismissal that came back by accident would be a four-hundred-row
        action next to a search box.
        """
        self._producto_pendiente()
        r = self.client.get("/insumos/mapeo")
        self.assertNotIn("Ninguno es insumo", r.text)
        self.assertNotIn("/insumos/mapeo/descartar", r.text)
        self.assertNotIn("Qué se está revisando", r.text)

    def test_el_filtro_es_en_vivo_y_no_tiene_segunda_plantilla(self):
        """Typing IS the filter: htmx re-requests this same route and
        picks `#cola` out of the answer, so a live filter cannot compute a
        different queue from the one a full page load shows (D16). The
        «Filtrar» button stays in the markup for the no-JS path and is
        hidden by CSS everywhere else."""
        self._producto_pendiente()
        r = self.client.get("/insumos/mapeo")
        self.assertIn('id="cola"', r.text)
        self.assertIn('hx-select="#cola"', r.text)
        self.assertIn('class="btn btn--ghost cribar__ir"', r.text)

    def test_el_porton_descarta_y_deshace_desde_la_pantalla(self):
        """The whole round trip through the real routes: the queue empties
        and the undo brings it back. `follow_redirects` because every
        mutation here answers a 303, same as the rest of the app."""
        self._producto_pendiente()
        antes = self.client.get("/insumos/mapeo").text
        self.assertIn("CLORO", antes)

        r = self.client.post(
            "/insumos/mapeo/descartar",
            data={"q": ""},
            follow_redirects=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("CLORO", r.text)

        lote = self.db.scalars(select(CatalogDescarte.lote)).first()
        volvio = self.client.post(
            "/insumos/mapeo/deshacer",
            data={"lote": lote, "q": ""},
            follow_redirects=True,
        )
        self.assertIn("CLORO", volvio.text)

    def test_el_porton_actua_sobre_el_alcance_guardado(self):
        """What replaces `test_una_bandera_vacia_no_invierte_el_filtro`.

        That test guarded a real trap: `receta` and `reciente` rode in the
        request as checkbox flags, an unchecked box posts `value=""`, and
        FastAPI reads an empty form value as *absent* and substitutes the
        default -- so the gate could count 2 and dismiss 1. The scope is app
        policy now (`app_setting`, changed on `/ajustes`), so it never rides
        in a request at all and that trap is structurally gone.

        The property underneath it has not gone anywhere: the set the gate
        dismisses is the set the button counted. This asserts it on both
        sides of a settings change, which is the only way the scope moves.
        """
        # NOT `_producto_pendiente`: this one needs the DEFAULT scope, which
        # that fixture deliberately widens.
        self.db.add(CatalogProduct(
            internal_code="96-0", upstream_product_id=96, supplier_id=96,
            supplier_nombre="Mayca", description="CLORO ECONOMICO MFS 1/1 GL",
            categoria_slug="limpieza", last_purchase_at=HOY,
        ))
        self.db.commit()

        # `limpieza` is outside the default scope, so the gate is offered
        # nothing and must dismiss nothing.
        self.client.post("/insumos/mapeo/descartar", data={"q": ""})
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(CatalogDescarte)), 0
        )

        # Widen it on /ajustes -- an omitted checkbox is the off state -- and
        # the same POST now covers it. The count is checked through the
        # service rather than off the screen: the gate has had no button
        # since 2026-09-07, but the route it answered still has to act on
        # the saved scope and not on a wider one.
        self.client.post("/ajustes/mapeo", data={"dias_reciente": "0"})
        alc = mapeo_svc.alcance(self.db)
        self.assertEqual(
            mapeo_svc.contar_filtro(
                self.db, solo_receta=alc.solo_receta,
                solo_reciente=alc.solo_reciente, dias_reciente=alc.dias_reciente,
            ),
            1,
        )
        self.client.post("/insumos/mapeo/descartar", data={"q": ""})
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(CatalogDescarte)), 1
        )

    def test_ajustes_guarda_el_alcance_de_la_cola(self):
        """The two flags are settings now, and the queue reports what was
        saved without ever offering to change it in place."""
        self.client.post(
            "/ajustes/mapeo", data={"solo_receta": "1", "dias_reciente": "45"}
        )
        r = self.client.get("/insumos/mapeo")
        self.assertIn("Alimentos, bebidas y empaques", r.text)
        self.assertIn("Últimos 45 días", r.text)
        self.assertNotIn('name="receta"', r.text)
        self.assertNotIn('name="reciente"', r.text)

        self.client.post("/ajustes/mapeo", data={"dias_reciente": "0"})
        r = self.client.get("/insumos/mapeo")
        self.assertIn("Todo el catálogo", r.text)
        self.assertIn("Sin límite de fecha", r.text)

    def test_un_numero_de_dias_ilegible_no_ensancha_la_cola(self):
        """Falling back to a default would silently widen what a bulk
        dismissal covers because someone typed «45 dias»."""
        self.client.post("/ajustes/mapeo", data={"solo_receta": "1", "dias_reciente": "45"})
        self.client.post("/ajustes/mapeo", data={"solo_receta": "1", "dias_reciente": "cuarenta"})
        r = self.client.get("/insumos/mapeo")
        self.assertIn("Últimos 45 días", r.text)

    def test_el_porton_no_descarta_categorias_que_no_le_pasaron(self):
        """The category picker rides in the POST body or the gate acts on a
        wider set than the button named. Same shape as the empty-flag bug
        above -- an absent repeated field is an absent *filter*, which here
        means «every category», not «none»."""
        self._producto_pendiente()
        self.client.post(
            "/insumos/mapeo/descartar",
            data={"q": "", "cat": "alimentos_carnes"},
        )
        # CLORO is `limpieza`, so a gate that honoured the category picked
        # dismisses nothing at all.
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(CatalogDescarte)), 0
        )

    def test_las_categorias_sobreviven_a_la_paginacion(self):
        """Every page link carries every filter. Dropping the categories
        from page two would widen the queue silently, and a page number
        would stop meaning the position the strip says it means."""
        self._producto_pendiente()
        r = self.client.get("/insumos/mapeo?cat=limpieza")
        self.assertEqual(r.status_code, 200)
        # The picked category is echoed into the POST forms AND onto the
        # queue's own links.
        self.assertIn('name="cat" value="limpieza"', r.text)

    def test_la_vista_de_cocina_no_lleva_ni_un_numero_de_plata(self):
        """D43, and the reason it is a service function and not a template
        that declines to print: a tablet on a pass is the least private
        screen in the building, and `arbol_cocina` has no cost field at all
        -- so this cannot be broken by adding one `{{ }}`."""
        r = self.client.get(f"/recetas/{self.plato.id}/cocina")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("20a1", r.text)
        self.assertIn("Pechuga deshuesada", r.text)
        self.assertIn("180", r.text)

    def test_la_vista_de_cocina_no_tiene_controles(self):
        """No costs and no controls, neither an oversight: it removes every
        accidental-edit risk from the one surface where hands are wet.

        Scoped to the recipe itself, not the page: the rail and the search
        sheet are the app's shell and are on every screen. What must not be
        here is a way to CHANGE this receta."""
        r = self.client.get(f"/recetas/{self.plato.id}/cocina")
        cuerpo = r.text.split('<div class="cocina">')[1].split("</main>")[0]
        for edicion in ("<form", "<input", "<button", "hx-post", "/lineas/"):
            with self.subTest(edicion=edicion):
                self.assertNotIn(edicion, cuerpo)

    def test_la_vista_de_cocina_sirve_una_receta_sin_precios(self):
        """A cook does not care that Sal has no price. `costear` would have
        answered this receta with an incomplete state; the kitchen view is
        not built on it, so it still says what goes in the plate."""
        r = self.client.get(f"/recetas/{self.plato.id}/cocina")
        self.assertIn("Salsa criolla", r.text)
        self.assertNotIn("incompleta", r.text)

    def test_el_delta_solo_aparece_cuando_algo_se_movio(self):
        """§3.4. The chip is the whole acknowledgement that the number
        responded to you, and it is computed by the SERVER -- there is no
        arithmetic in the browser (D16). It must be absent on a page load
        (§5: nothing animates on load) and absent when an edit moved
        nothing, or it stops meaning anything."""
        # Its own receta, and a COMPLETE one: the shared fixture plato is
        # deliberately incomplete (Sal has no price), so its total is None
        # at both ends and `_delta` correctly refuses to invent a move.
        plato = recetas_svc.create(
            self.db, nombre="Solo pechuga", tipo="plato", porciones="1"
        )
        linea = recetas_svc.agregar_linea(
            self.db, plato, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="180",
        )
        url = f"/recetas/{plato.id}/lineas/{linea.id}"
        hx = {"hx-request": "true"}

        subio = self.client.post(url, data={"cantidad": "200", "unidad": "g"}, headers=hx)
        self.assertIn("delta--up", subio.text)

        igual = self.client.post(url, data={"cantidad": "200", "unidad": "g"}, headers=hx)
        self.assertNotIn('class="delta', igual.text)

        bajo = self.client.post(url, data={"cantidad": "180", "unidad": "g"}, headers=hx)
        self.assertIn("delta--down", bajo.text)

        carga = self.client.get(f"/recetas/{plato.id}")
        self.assertNotIn('class="delta', carga.text)

    def test_el_simulador_no_escribe_por_la_ruta_real(self):
        """The route, not just the service: a POST to /simular must leave
        the database exactly as it found it, htmx shape and plain shape
        alike (D42, D35)."""
        linea = next(ln for ln in self.plato.lineas if ln.tipo_componente == "insumo")
        for cabeceras in ({}, {"hx-request": "true"}):
            with self.subTest(hx=bool(cabeceras)):
                r = self.client.post(
                    f"/recetas/{self.plato.id}/simular",
                    data={f"cant-{linea.id}": "999"},
                    headers=cabeceras,
                )
                self.assertEqual(r.status_code, 200)
                self.db.expire_all()
                self.assertEqual(
                    self.db.get(RecetaLinea, linea.id).cantidad, Decimal("180.0000")
                )

    def test_guardar_el_simulador_pasa_por_la_validacion_de_siempre(self):
        """The simulador gets no private way into the database: «Guardar»
        goes through `svc.actualizar_linea` like every other edit."""
        linea = next(ln for ln in self.plato.lineas if ln.tipo_componente == "insumo")
        r = self.client.post(
            f"/recetas/{self.plato.id}/simular/guardar",
            data={f"cant-{linea.id}": "200"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        self.db.expire_all()
        self.assertEqual(self.db.get(RecetaLinea, linea.id).cantidad, Decimal("200.0000"))

    def test_simular_una_preparacion_nombra_los_platos_que_arrastra(self):
        r = self.client.get(f"/recetas/{self.salsa.id}/simular")
        self.assertIn("Qué se mueve si guardás esto", r.text)
        self.assertIn("Pollo a la plancha", r.text)

    def test_buscar_responde_las_dos_formas(self):
        """One route, two shapes (D35). The whole page is what makes search
        exist with JavaScript off: a <dialog> cannot be opened without
        script, so without it Ctrl-K would be the only way in."""
        pagina = self.client.get("/buscar?q=pech")
        self.assertIn("<html", pagina.text)
        fragmento = self.client.get("/buscar?q=pech", headers={"hx-request": "true"})
        self.assertNotIn("<html", fragmento.text)
        for r in (pagina, fragmento):
            self.assertIn("Pechuga deshuesada", r.text)

    def test_el_buscador_no_se_dibuja_dos_veces_en_su_propia_pagina(self):
        """`/buscar` IS the search, so the rail's sheet would put
        `id="buscar-hits"` and `id="hit-primero"` on the page twice --
        invalid, and Enter would follow whichever came first."""
        r = self.client.get("/buscar?q=pech")
        self.assertEqual(r.text.count('id="buscar-hits"'), 1)
        self.assertEqual(r.text.count('id="hit-primero"'), 1)
        self.assertNotIn('id="buscador"', r.text)

    def test_el_buscador_esta_en_las_demas_pantallas(self):
        r = self.client.get("/recetas")
        self.assertIn('id="buscador"', r.text)
        self.assertIn('id="abrir-buscador"', r.text)

    def test_el_arranque_no_aparece_en_una_instalacion_ya_armada(self):
        """The fixture has a sync-less install but real insumos, fuentes and
        recetas, so some steps are done and some are not -- the card shows.
        Once all four are done it must be gone for good."""
        from app.models import CatalogSyncRun

        self.assertIn("Para empezar", self.client.get("/recetas").text)
        self.db.add(CatalogSyncRun(status="ok"))
        self.db.commit()
        self.assertNotIn("Para empezar", self.client.get("/recetas").text)

    def test_el_arranque_solo_sale_en_el_menu(self):
        """It shows on the screen `/` lands on and nowhere else: a checklist
        repeated on six screens is nagging."""
        self.assertNotIn("Para empezar", self.client.get("/preparaciones").text)

    def test_confirmar_avanza_en_la_cola_en_vez_de_volver_al_principio(self):
        """The whole round trip: confirming the product on page 2 leaves the
        operator on page 2, now showing what used to be page 3 -- not back on
        page 1, which is where 300 rows of work used to send them."""
        # Equal purchase counts, so the tiebreak is `description` -- these
        # names are chosen to make the queue order obvious and stable.
        for i, nombre in enumerate(("AAA", "BBB", "CCC")):
            self.db.add(CatalogProduct(
                internal_code=f"98-{i}", upstream_product_id=98 + i, supplier_id=98,
                supplier_nombre="Mayca", description=nombre,
                categoria_slug="alimentos_carnes", last_purchase_at=HOY,
            ))
        self.db.commit()

        pagina2 = self.client.get("/insumos/mapeo?desde=1")
        self.assertIn('<p class="mapeo__desc">BBB</p>', pagina2.text)

        r = self.client.post(
            "/insumos/mapeo/confirmar",
            data={
                "catalog_product_id": self.db.scalars(
                    select(CatalogProduct.id).where(CatalogProduct.description == "BBB")
                ).one(),
                "nombre_nuevo": "Bbb", "unidad_base": "g", "factor": "1000",
                "q": "", "desde": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        self.assertIn("desde=1", r.headers["location"])

        # Same page number, now showing what used to be behind BBB.
        siguiente = self.client.get(r.headers["location"])
        self.assertIn('<p class="mapeo__desc">CCC</p>', siguiente.text)
        self.assertNotIn('<p class="mapeo__desc">BBB</p>', siguiente.text)

    def test_la_cola_crea_el_insumo_con_merma_y_metodo(self):
        """The fields are on the card and they reach the row."""
        self.db.add(CatalogProduct(
            internal_code="97-0", upstream_product_id=97, supplier_id=97,
            supplier_nombre="Mayca", description="OSOBUCO",
            categoria_slug="alimentos_carnes", last_purchase_at=HOY,
        ))
        self.db.commit()
        pid = self.db.scalars(
            select(CatalogProduct.id).where(CatalogProduct.description == "OSOBUCO")
        ).one()

        pantalla = self.client.get("/insumos/mapeo")
        self.assertIn('name="merma_pct"', pantalla.text)
        self.assertIn('name="metodo_costeo"', pantalla.text)

        r = self.client.post(
            "/insumos/mapeo/confirmar",
            data={
                "catalog_product_id": pid, "nombre_nuevo": "Osobuco",
                "unidad_base": "g", "merma_pct": "18,5",
                "metodo_costeo": "max_window", "factor": "1000",
                "q": "", "desde": "0",
            },
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        insumo = self.db.scalars(select(Insumo).where(Insumo.nombre == "Osobuco")).one()
        self.assertEqual(insumo.merma_pct, Decimal("18.5"))
        self.assertEqual(insumo.metodo_costeo, "max_window")

    def test_una_merma_imposible_devuelve_la_pantalla_no_un_500(self):
        """Validation refusing must not cost the operator the card they had
        filled in -- the message lands beside the field and the values stay."""
        self.db.add(CatalogProduct(
            internal_code="96-0", upstream_product_id=96, supplier_id=96,
            supplier_nombre="Mayca", description="POLLO ENTERO",
            categoria_slug="alimentos_pollo", last_purchase_at=HOY,
        ))
        self.db.commit()
        pid = self.db.scalars(
            select(CatalogProduct.id).where(CatalogProduct.description == "POLLO ENTERO")
        ).one()

        r = self.client.post(
            "/insumos/mapeo/confirmar",
            data={
                "catalog_product_id": pid, "nombre_nuevo": "Pollo entero",
                "unidad_base": "g", "merma_pct": "995", "metodo_costeo": "",
                "factor": "1000", "q": "", "receta": "0", "reciente": "0",
                "desde": "0",
            },
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("La merma tiene que ser menor", r.text)
        # What they typed is still in the field, and nothing was written.
        self.assertIn('value="995"', r.text)
        self.assertIn("Pollo entero", r.text)
        self.assertEqual(
            self.db.scalars(select(Insumo).where(Insumo.nombre == "Pollo entero")).all(),
            [],
        )

    def test_agrupar_abre_el_modal_y_lo_sondea(self):
        """The button answers with a <dialog> that polls its own status.

        `iniciar_agrupacion` is patched out: it spawns a daemon thread with
        its OWN Session, which in a test would reach past the SQLite
        harness for the real database. What is under test here is the
        modal, not the thread -- the job itself is covered directly in
        test_mapeo.py.
        """
        corriendo = mapeo_svc.EstadoAgrupacion(running=True, total=405, procesados=7)
        with mock.patch.object(
            mapeo_svc, "iniciar_agrupacion", return_value=True
        ), mock.patch.object(
            mapeo_svc, "estado_agrupacion", return_value=corriendo
        ):
            r = self.client.post("/ajustes/agrupar")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<dialog", r.text)
        self.assertIn("data-block-close", r.text)
        self.assertIn("/ajustes/agrupar/estado?target=modal", r.text)
        self.assertIn("7 de 405", r.text)

    def test_el_modal_ocioso_no_sale_en_blanco(self):
        # Pressing it with nothing to do must say so, not open an empty box.
        with mock.patch.object(
            mapeo_svc, "iniciar_agrupacion", return_value=False
        ), mock.patch.object(
            mapeo_svc, "estado_agrupacion", return_value=mapeo_svc.EstadoAgrupacion()
        ):
            r = self.client.post("/ajustes/agrupar")
        self.assertIn("No hay productos pendientes de agrupar", r.text)
        self.assertIn("Cerrar", r.text)
    def test_el_estado_se_puede_sondear_solo(self):
        r = self.client.get("/ajustes/agrupar/estado?target=modal")
        self.assertEqual(r.status_code, 200)

    def test_el_panel_ofrece_agrupar_cuando_falta(self):
        self.db.add(CatalogProduct(
            internal_code="95-0", upstream_product_id=95, supplier_id=95,
            supplier_nombre="Mayca", description="OSOBUCO",
            categoria_slug="alimentos_carnes", last_purchase_at=HOY,
        ))
        self.db.commit()
        r = self.client.get("/ajustes")
        self.assertIn('hx-post="/ajustes/agrupar"', r.text)
        self.assertIn('hx-target="#modal-host"', r.text)

    def test_el_host_del_modal_existe_en_toda_pagina(self):
        # The dialog opens itself on swap into #modal-host, so the host has
        # to be on every page the button can be pressed from -- including
        # /buscar, which sits inside its own template guard.
        for url in ("/ajustes", "/insumos", "/buscar"):
            self.assertIn('id="modal-host"', self.client.get(url).text, url)

    def _producto_pendiente(self) -> CatalogProduct:
        """One unmapped, non-food product -- the gate's whole reason to
        exist, and the shape the live catalog is full of.

        It also widens the SAVED scope, because `limpieza` is exactly what
        the default scope keeps out. Tests used to widen it per request
        (`?receta=0&reciente=0`); the scope is app policy now, so widening
        it is a settings write and belongs with the fixture that needs it.
        """
        mapeo_svc.guardar_alcance(self.db, solo_receta=False, dias_reciente=0)
        p = CatalogProduct(
            internal_code="99-0001", upstream_product_id=99, supplier_id=99,
            supplier_nombre="Mayca", description="CLORO ECONOMICO MFS 1/1 GL",
            categoria_slug="limpieza", last_purchase_at=HOY,
        )
        self.db.add(p)
        self.db.commit()
        return p


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
