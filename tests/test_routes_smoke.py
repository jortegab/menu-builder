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

from app.models import CatalogPricePoint, CatalogProduct, InsumoFuente  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402
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
            categoria_slug="insumos_carnes",
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
            "/recetas/nueva",
            "/recetas/nueva?tipo=preparacion",
            "/preparaciones",
            "/insumos",
            "/insumos/nuevo",
            "/insumos/mapeo",
            "/ajustes",
            f"/insumos/{self.pechuga.id}",
            f"/insumos/{self.pechuga.id}/editar",
            f"/recetas/{self.plato.id}",
            f"/recetas/{self.plato.id}/editar",
            f"/recetas/{self.salsa.id}",
            f"/recetas/{self.plato.id}/paleta",
            f"/recetas/{self.plato.id}/paleta?q=pech",
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
