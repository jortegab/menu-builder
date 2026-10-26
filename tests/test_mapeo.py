"""`services/mapeo.py` -- the mapping queue.

The tests that matter most here are the ones about what the queue *refuses*
to do on its own: nothing reaches `confirmed` without a person, a rejected
suggestion is never re-offered, and an unresolved factor stays None rather
than becoming a 1.
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

from sqlalchemy import select  # noqa: E402
from app.models import (  # noqa: E402
    CatalogAgrupacion,
    CatalogParecido,
    CatalogPricePoint,
    CatalogProduct,
    Insumo,
    InsumoFuente,
)
from app.services import insumos as insumos_svc  # noqa: E402
from app.services.insumos import ValidationError  # noqa: E402
from app.services import llm_insumo  # noqa: E402
from app.services import mapeo as svc  # noqa: E402


class MapeoTestCase(DbTestCase):
    def producto(self, description="PECHUGA DESHUESADA MFS 1/5KG", **kw):
        p = CatalogProduct(
            internal_code=kw.pop("internal_code", f"8-{description[:6]}"),
            upstream_product_id=kw.pop("upstream_product_id", 1),
            supplier_id=kw.pop("supplier_id", 8),
            supplier_nombre=kw.pop("supplier_nombre", "Mayca"),
            description=description,
            categoria_slug=kw.pop("categoria_slug", "alimentos_pollo"),
            unidad_medida=kw.pop("unidad_medida", "Unid"),
            last_purchase_at=kw.pop("last_purchase_at", dt.date.today()),
            **kw,
        )
        self.db.add(p)
        self.db.commit()
        return p

    def compras(self, producto, n):
        for i in range(n):
            self.db.add(
                CatalogPricePoint(
                    catalog_product_id=producto.id,
                    clave=f"{producto.id:03d}" + "0" * 44 + f"{i:03d}",
                    numero_linea=i,
                    fecha=dt.date.today(),
                    cantidad=Decimal(1),
                    precio_efectivo=Decimal(1000),
                    precio_efectivo_crc=Decimal(1000),
                )
            )
        self.db.commit()


class TestCola(MapeoTestCase):
    def test_lists_unmapped_products(self):
        p = self.producto()
        self.assertEqual([x.id for x, _ in svc.pendientes(self.db)], [p.id])

    def test_a_confirmed_product_leaves_the_queue(self):
        p = self.producto()
        svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga", factor=Decimal(5000))
        self.assertEqual(svc.pendientes(self.db), [])

    def test_ignored_products_never_appear(self):
        self.producto(description="DESCONTINUADO", ignored=True, internal_code="8-ign")
        self.assertEqual(svc.pendientes(self.db), [])

    def test_non_food_categories_are_filtered_by_default(self):
        # 415 products on the live catalog. Every one is a row the operator
        # would have to skip past to reach the food.
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        self.assertEqual(svc.pendientes(self.db), [])
        self.assertEqual(len(svc.pendientes(self.db, solo_receta=False)), 1)

    def test_stale_products_are_filtered_by_default(self):
        viejo = dt.date.today() - dt.timedelta(days=svc.DIAS_RECIENTE + 10)
        self.producto(description="ALGO VIEJO", last_purchase_at=viejo, internal_code="8-old")
        self.assertEqual(svc.pendientes(self.db), [])
        self.assertEqual(len(svc.pendientes(self.db, solo_reciente=False)), 1)

    def test_busiest_products_come_first(self):
        # Mapping pays off in purchase-count order; alphabetical buries the
        # ingredient bought 98 times behind one bought once.
        poco = self.producto(description="AAA POCO", internal_code="8-a", upstream_product_id=1)
        mucho = self.producto(description="ZZZ MUCHO", internal_code="8-z", upstream_product_id=2)
        self.compras(poco, 1)
        self.compras(mucho, 5)
        self.assertEqual([p.id for p, _ in svc.pendientes(self.db)], [mucho.id, poco.id])

    def test_search_filters_by_description(self):
        self.producto(description="PECHUGA", internal_code="8-p", upstream_product_id=1)
        self.producto(description="ACEITE", internal_code="8-a", upstream_product_id=2)
        self.assertEqual([p.description for p, _ in svc.pendientes(self.db, q="pech")], ["PECHUGA"])


class TestSugerencia(MapeoTestCase):
    def test_proposes_a_new_insumo_when_nothing_matches(self):
        p = self.producto()
        s = svc.sugerir(self.db, p)
        self.assertIsNone(s.insumo_id)
        self.assertTrue(s.nombre_propuesto)
        self.assertEqual(s.origen, "ninguna")

    def test_matches_an_existing_insumo_by_similarity(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        s = svc.sugerir(self.db, self.producto())
        self.assertEqual(s.insumo_id, insumo.id)
        self.assertEqual(s.insumo_nombre, "Pechuga deshuesada")
        self.assertEqual(s.origen, "similitud")

    def test_reads_the_factor_from_the_description(self):
        insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        s = svc.sugerir(self.db, self.producto())
        self.assertEqual(s.factor, Decimal(5000))
        self.assertEqual(s.factor_origen, "descripcion")

    def test_unresolved_factor_stays_none(self):
        # Never a 1: that would silently cost the insumo per package.
        p = self.producto(description="TORTILLA BLANCA", categoria_slug="alimentos_abarrotes")
        s = svc.sugerir(self.db, p)
        self.assertIsNone(s.factor)
        self.assertEqual(s.factor_origen, "sin_resolver")

    def test_a_rejected_insumo_is_not_offered_again(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        p = self.producto()
        self.assertEqual(svc.sugerir(self.db, p).insumo_id, insumo.id)

        svc.rechazar(self.db, catalog_product_id=p.id, insumo_id=insumo.id)
        again = svc.sugerir(self.db, p)
        self.assertIsNone(again.insumo_id)
        self.assertTrue(again.nombre_propuesto)

    def test_archived_insumos_are_not_suggested(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        insumos_svc.set_activo(self.db, insumo, False)
        self.assertIsNone(svc.sugerir(self.db, self.producto()).insumo_id)

    def test_rejecting_twice_is_harmless(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        p = self.producto()
        svc.rechazar(self.db, catalog_product_id=p.id, insumo_id=insumo.id)
        svc.rechazar(self.db, catalog_product_id=p.id, insumo_id=insumo.id)
        self.assertIsNone(svc.sugerir(self.db, p).insumo_id)


class TestConfirmar(MapeoTestCase):
    def test_creates_the_insumo_and_the_source(self):
        p = self.producto()
        fuente = svc.confirmar(
            self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga deshuesada",
            unidad_base="g", factor=Decimal(5000), factor_origen="descripcion",
        )
        insumo = self.db.get(Insumo, fuente.insumo_id)
        self.assertEqual(insumo.nombre, "Pechuga deshuesada")
        self.assertEqual(insumo.slug, "pechuga-deshuesada")
        self.assertEqual(fuente.factor_conversion, Decimal(5000))

    def test_confirmation_is_always_human_and_confirmed(self):
        # Non-negotiable #4: only a person reaches this function, and only
        # `confirmed` rows ever feed a price series.
        p = self.producto()
        fuente = svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga")
        self.assertEqual(fuente.status, "confirmed")
        self.assertEqual(fuente.mapped_by, "human")
        self.assertIsNotNone(fuente.confirmed_at)

    def test_attaches_a_second_product_to_the_same_insumo(self):
        # The whole point: two suppliers, one insumo.
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        a = self.producto(description="FILETE PECHUGA MFS 1/5KG", internal_code="8-a", upstream_product_id=1)
        b = self.producto(description="PECHUGA DESHUESADA", internal_code="11-b", upstream_product_id=2, supplier_id=11)
        svc.confirmar(self.db, catalog_product_id=a.id, insumo_id=insumo.id, factor=Decimal(5000))
        svc.confirmar(self.db, catalog_product_id=b.id, insumo_id=insumo.id, factor=Decimal(1000))
        fuentes = self.db.query(InsumoFuente).filter_by(insumo_id=insumo.id).all()
        self.assertEqual(len(fuentes), 2)
        self.assertEqual({f.factor_conversion for f in fuentes}, {Decimal(5000), Decimal(1000)})

    def test_a_missing_factor_records_sin_resolver(self):
        p = self.producto()
        fuente = svc.confirmar(
            self.db, catalog_product_id=p.id, nombre_nuevo="Algo",
            factor=None, factor_origen="descripcion",
        )
        self.assertIsNone(fuente.factor_conversion)
        self.assertEqual(fuente.factor_origen, "sin_resolver")

    def test_new_insumo_needs_a_name(self):
        p = self.producto()
        with self.assertRaises(ValueError):
            svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="   ")

    def test_confirming_again_updates_rather_than_duplicating(self):
        p = self.producto()
        svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga", factor=Decimal(1000))
        svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga 2", factor=Decimal(5000))
        fuentes = self.db.query(InsumoFuente).filter_by(catalog_product_id=p.id).all()
        self.assertEqual(len(fuentes), 1)
        self.assertEqual(fuentes[0].factor_conversion, Decimal(5000))


class TestResumen(MapeoTestCase):
    def test_counts(self):
        a = self.producto(description="A", internal_code="8-a", upstream_product_id=1)
        self.producto(description="B", internal_code="8-b", upstream_product_id=2)
        r = svc.resumen(self.db)
        self.assertEqual((r["total"], r["mapeados"], r["pendientes"]), (2, 0, 2))

        svc.confirmar(self.db, catalog_product_id=a.id, nombre_nuevo="A")
        r = svc.resumen(self.db)
        self.assertEqual((r["mapeados"], r["pendientes"]), (1, 1))

    def test_llm_flag_reflects_configuration(self):
        # `dbcase` stubs the LLM off for every test, so the queue advertises
        # string similarity here -- and still works. That stub is what keeps
        # the suite hermetic now that a real key exists in `.env`.
        self.assertFalse(svc.resumen(self.db)["llm"])


class TestPorton(MapeoTestCase):
    """The bulk gate (DESIGN.md §3.7). A large share of a 1 420-product
    catalog will never be an ingredient, and a queue that makes you say so
    one row at a time is a page that punishes you for having work to do."""

    def test_descartar_saca_el_producto_de_la_cola(self):
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        _lote, n = svc.descartar_filtro(self.db, q="cloro", solo_receta=False)
        self.assertEqual(n, 1)
        self.assertEqual(svc.pendientes(self.db, solo_receta=False), [])


    def test_el_filtro_admite_empaques_bebidas_y_alimentos(self):
        # The filter is an allowlist of families that can be costed into a
        # plate. `empaques` is in it deliberately: a plate that leaves in a
        # container pays for the container.
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="9-a")
        self.producto(description="VINO", categoria_slug="bebidas_alcoholicas", internal_code="9-b")
        self.producto(description="CAJA PARA LLEVAR", categoria_slug="empaques", internal_code="9-c")
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="9-d")
        self.producto(description="ESCOBA", categoria_slug="menaje", internal_code="9-e")
        vistos = {p.description for p, _ in svc.pendientes(self.db)}
        self.assertEqual(vistos, {"POLLO", "VINO", "CAJA PARA LLEVAR"})

    def test_el_filtro_admite_una_hoja_nueva_de_una_familia_ya_elegida(self):
        # Prefix, not a list of leaves: upstream adds leaves to its own
        # taxonomy (`alimentos_aceites` appeared on 2026-09-02) and a new
        # one under a family already chosen must not need a code change
        # here. A brand-new *family* still does, which is the point.
        self.producto(description="ACEITE", categoria_slug="alimentos_loquesea", internal_code="9-f")
        self.producto(description="SERVILLETA", categoria_slug="higiene_nueva", internal_code="9-g")
        vistos = {p.description for p, _ in svc.pendientes(self.db)}
        self.assertEqual(vistos, {"ACEITE"})

    def test_una_categoria_sin_asignar_no_entra_sola(self):
        # NULL coalesces to "" and matches no family. A product upstream
        # has not categorised is not evidence that it belongs in a recipe.
        self.producto(description="MISTERIO", categoria_slug=None, internal_code="9-h")
        self.assertEqual(svc.pendientes(self.db), [])
        self.assertEqual(len(svc.pendientes(self.db, solo_receta=False)), 1)
    def test_el_conteo_y_la_accion_ven_lo_mismo(self):
        # The one bug on this screen that would be invisible: the button
        # names its own size, so a count computed from different filters
        # than the dismissal would have the operator approve one number and
        # get another. Both go through `_filtros` for exactly this reason.
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="8-pol")
        antes = svc.contar_filtro(self.db, solo_receta=False)
        _lote, n = svc.descartar_filtro(self.db, solo_receta=False)
        self.assertEqual(antes, n)
        self.assertEqual(svc.contar_filtro(self.db, solo_receta=False), 0)

    def test_el_conteo_y_la_accion_ven_las_mismas_categorias(self):
        # Same property as the test above, now that the gate has a fourth
        # filter. A category picker the count honours and the dismissal
        # forgets would dismiss the whole catalog on a button that said 40.
        self.producto(description="ZANAHORIA", categoria_slug="alimentos_verduras", internal_code="c-1")
        self.producto(description="APIO", categoria_slug="alimentos_verduras", internal_code="c-2")
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="c-3")
        antes = svc.contar_filtro(self.db, categorias=["alimentos_verduras"])
        self.assertEqual(antes, 2)
        _lote, n = svc.descartar_filtro(self.db, categorias=["alimentos_verduras"])
        self.assertEqual(antes, n)
        vistos = {prod.description for prod, _ in svc.pendientes(self.db)}
        self.assertEqual(vistos, {"POLLO"})

    def test_la_categoria_es_un_conjunto_no_un_valor(self):
        # «Verduras y frutas» is one pass through the queue. A picker that
        # takes one at a time is how the second one never happens.
        self.producto(description="ZANAHORIA", categoria_slug="alimentos_verduras", internal_code="c-4")
        self.producto(description="MANGO", categoria_slug="alimentos_frutas", internal_code="c-5")
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="c-6")
        vistos = {
            prod.description
            for prod, _ in svc.pendientes(
                self.db, categorias=["alimentos_verduras", "alimentos_frutas"]
            )
        }
        self.assertEqual(vistos, {"ZANAHORIA", "MANGO"})

    def test_sin_categorias_no_filtra_nada(self):
        # The resting state of the picker is the whole catalog, never "no
        # categories" -- an empty list that meant "match nothing" would show
        # an empty queue on first load and read as "there is no work".
        self.producto(description="ZANAHORIA", categoria_slug="alimentos_verduras", internal_code="c-7")
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="c-8")
        self.assertEqual(svc.contar_filtro(self.db, categorias=[]), 2)
        self.assertEqual(svc.contar_filtro(self.db), 2)

    def test_las_facetas_no_se_vacian_a_si_mismas(self):
        # A facet count that applied its OWN filter would leave exactly one
        # category on screen the moment one was ticked, and a second could
        # never be added. The other filters DO apply, so the numbers are the
        # ones the operator would actually get.
        self.producto(description="ZANAHORIA", categoria_slug="alimentos_verduras", internal_code="c-9")
        self.producto(description="APIO", categoria_slug="alimentos_verduras", internal_code="c-10")
        self.producto(description="MANGO", categoria_slug="alimentos_frutas", internal_code="c-11")
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="c-12")
        facetas = {f["slug"]: f["n"] for f in svc.facetas_categoria(self.db)}
        self.assertEqual(facetas, {"alimentos_verduras": 2, "alimentos_frutas": 1})
        # Busiest first, the same order the queue itself is in.
        self.assertEqual(
            [f["slug"] for f in svc.facetas_categoria(self.db)],
            ["alimentos_verduras", "alimentos_frutas"],
        )

    def test_las_facetas_ofrecen_el_bucket_sin_categoria(self):
        # An uncategorised product is exactly the kind that hides from every
        # filter and never gets decided, so the empty slug is a real bucket
        # that can be ticked -- and it sorts last regardless of size,
        # because it is a fallback and not a category.
        self.producto(description="MISTERIO", categoria_slug=None, internal_code="c-13")
        self.producto(description="OTRO", categoria_slug=None, internal_code="c-14")
        self.producto(description="MANGO", categoria_slug="alimentos_frutas", internal_code="c-15")
        facetas = svc.facetas_categoria(self.db, solo_receta=False)
        self.assertEqual([f["slug"] for f in facetas], ["alimentos_frutas", ""])
        vistos = {
            prod.description
            for prod, _ in svc.pendientes(self.db, solo_receta=False, categorias=[""])
        }
        self.assertEqual(vistos, {"MISTERIO", "OTRO"})

    def test_un_lote_se_deshace_entero(self):
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        self.producto(description="JABON", categoria_slug="limpieza", internal_code="8-jab")
        lote, n = svc.descartar_filtro(self.db, solo_receta=False)
        self.assertEqual(n, 2)
        self.assertEqual(svc.deshacer_descarte(self.db, lote), 2)
        self.assertEqual(len(svc.pendientes(self.db, solo_receta=False)), 2)

    def test_el_descarte_no_toca_el_espejo(self):
        # Non-negotiable #3: the mirror is derived and disposable, and
        # `CatalogProduct.ignored` is UPSTREAM's flag -- catalog_sync
        # overwrites it from the payload every run, so a local write there
        # would be silently reverted by the next sync. The decision lives
        # in its own table instead.
        p = self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        svc.descartar_filtro(self.db, solo_receta=False)
        self.db.refresh(p)
        self.assertFalse(p.ignored)

    def test_el_resumen_separa_descartados_de_pendientes(self):
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="8-pol")
        svc.descartar_filtro(self.db, q="cloro", solo_receta=False)
        r = svc.resumen(self.db)
        self.assertEqual(r["descartados"], 1)
        self.assertEqual(r["pendientes"], 1)

    def test_la_insignia_no_promete_trabajo_ya_descartado(self):
        # The nav badge and the queue share `_filtros`, so the badge cannot
        # count work the screen will not then show.
        self.producto(description="POLLO", categoria_slug="alimentos_carnes", internal_code="8-pol")
        self.assertEqual(svc.contar_pendientes(self.db), 1)
        svc.descartar_filtro(self.db)
        self.assertEqual(svc.contar_pendientes(self.db), 0)

    def test_saltar_avanza_sin_recordar_nada(self):
        # Skipping is not rejecting: the product comes back on the next
        # pass, and only a person rejecting something is remembered.
        self.producto(description="UNO", categoria_slug="alimentos_carnes", internal_code="8-a")
        self.producto(description="DOS", categoria_slug="alimentos_carnes", internal_code="8-b")
        primero = svc.pendientes(self.db, limite=1)
        segundo = svc.pendientes(self.db, limite=1, desde=1)
        self.assertNotEqual(primero[0][0].id, segundo[0][0].id)
        self.assertEqual(len(svc.pendientes(self.db)), 2)

class TestInsumoNuevoDesdeLaCola(MapeoTestCase):
    """Creating an insumo from the queue writes the same row the insumo form
    writes -- including merma and método, and under the same validation."""

    def test_guarda_merma_y_metodo(self):
        p = self.producto(description="OSOBUCO", categoria_slug="alimentos_carnes")
        svc.confirmar(
            self.db, catalog_product_id=p.id, nombre_nuevo="Osobuco",
            unidad_base="g", merma_pct="18,5", metodo_costeo="max_window",
            factor=Decimal(1000),
        )
        insumo = self.db.scalars(select(Insumo)).one()
        self.assertEqual(insumo.merma_pct, Decimal("18.5"))
        self.assertEqual(insumo.metodo_costeo, "max_window")

    def test_vacios_significan_el_valor_por_defecto(self):
        # NULL método means "follow the app default as it changes", not
        # "unset" -- the same reading the insumo form gives it.
        p = self.producto(description="SAL", categoria_slug="alimentos_abarrotes")
        svc.confirmar(
            self.db, catalog_product_id=p.id, nombre_nuevo="Sal",
            unidad_base="g", factor=Decimal(1000),
        )
        insumo = self.db.scalars(select(Insumo)).one()
        self.assertEqual(insumo.merma_pct, Decimal(0))
        self.assertIsNone(insumo.metodo_costeo)

    def test_una_merma_imposible_se_rechaza(self):
        # The queue used to be the one door into this table that skipped the
        # insumo form's rules entirely.
        p = self.producto(description="POLLO", categoria_slug="alimentos_pollo")
        with self.assertRaises(ValidationError) as caja:
            svc.confirmar(
                self.db, catalog_product_id=p.id, nombre_nuevo="Pollo",
                unidad_base="g", merma_pct="995", factor=Decimal(1000),
            )
        self.assertIn("merma_pct", caja.exception.errors)
        self.assertEqual(self.db.scalars(select(Insumo)).all(), [])

    def test_un_metodo_inventado_se_rechaza(self):
        p = self.producto(description="POLLO", categoria_slug="alimentos_pollo")
        with self.assertRaises(ValidationError) as caja:
            svc.confirmar(
                self.db, catalog_product_id=p.id, nombre_nuevo="Pollo",
                unidad_base="g", metodo_costeo="lo_que_sea", factor=Decimal(1000),
            )
        self.assertIn("metodo_costeo", caja.exception.errors)

    def test_el_grupo_hereda_el_mismo_insumo_una_sola_vez(self):
        # Merma and método belong to the insumo, so a group of three pack
        # sizes gets one insumo carrying them, not three.
        a = self.producto(description="OSOBUCO", internal_code="g-a",
                          categoria_slug="alimentos_carnes")
        b = self.producto(description="OSOBUCO PICADO", internal_code="g-b",
                          categoria_slug="alimentos_carnes")
        svc.confirmar_muchos(
            self.db, catalog_product_ids=[a.id, b.id], nombre_nuevo="Osobuco",
            unidad_base="g", merma_pct="12", metodo_costeo="median_window",
            factores={a.id: Decimal(1000), b.id: Decimal(3000)},
        )
        insumos = self.db.scalars(select(Insumo)).all()
        self.assertEqual(len(insumos), 1)
        self.assertEqual(insumos[0].merma_pct, Decimal(12))
        fuentes = self.db.scalars(select(InsumoFuente)).all()
        self.assertEqual({f.factor_conversion for f in fuentes},
                         {Decimal(1000), Decimal(3000)})



class TestParecidos(MapeoTestCase):
    """Grouping the look-alikes is what makes a 1 435-row queue tolerable:
    the live catalog carries the same ingredient written three ways, and
    deciding those dozens of cards apart is how a duplicate insumo gets
    created — which splits one ingredient's price history in half with
    nothing looking wrong on any screen."""

    def test_un_empaque_nunca_se_agrupa_con_comida(self):
        # Once `empaques` joined the queue the two families started
        # colliding on shared adjectives: the noise set strips "bolsas",
        # which is the one word saying what the thing is, so only "grande"
        # was left to compare. A bag is not an ingredient.
        pepino = self.producto(
            description="PEPINOS GRANDE", categoria_slug="alimentos_verduras",
            internal_code="9-p",
        )
        self.producto(
            description="BOLSAS VIKINGO GRANDE", categoria_slug="empaques",
            internal_code="9-q",
        )
        grupo = svc.parecidos(self.db, pepino, solo_receta=False, solo_reciente=False)
        self.assertEqual(grupo, [])

    def test_dos_empaques_si_se_agrupan_entre_ellos(self):
        # The fence runs between the families, not around packaging: two
        # bags are still one insumo in two pack sizes.
        a = self.producto(
            description="BOLSA ROLLO NACIONAL 7X10", categoria_slug="empaques",
            internal_code="9-r",
        )
        self.producto(
            description="BOLSA ROLLO NACIONAL 14X20", categoria_slug="empaques",
            internal_code="9-s",
        )
        grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual([p.producto.description for p in grupo],
                         ["BOLSA ROLLO NACIONAL 14X20"])

    def test_agrupa_la_misma_cosa_en_dos_presentaciones(self):
        a = self.producto(description="QUESO MOZZARELLA RALLADO LEPRINO 1/15 LB", internal_code="9-a")
        self.producto(description="QUESO MOZZARELLA RALLADO LEPRINO 1/2,27 KG", internal_code="9-b")
        grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual([p.producto.description for p in grupo],
                         ["QUESO MOZZARELLA RALLADO LEPRINO 1/2,27 KG"])
        self.assertTrue(grupo[0].sugerido)

    def test_una_tilde_no_es_otro_insumo(self):
        a = self.producto(description="AZUCAR BLANCO DONA MARIA 5 KG", internal_code="9-c")
        self.producto(description="AZUCAR BLANCO DOÑA MARIA 5 KG", internal_code="9-d")
        grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual(len(grupo), 1)
        self.assertTrue(grupo[0].sugerido)

    def test_no_agrupa_cosas_distintas_que_empiezan_igual(self):
        """The over-merge guard is the whole safety of this feature, and it
        is `insumo_match.similitud`'s existing conflict rule — not a second
        opinion invented here."""
        a = self.producto(description="AZUCAR BLANCO DONA MARIA 5 KG", internal_code="9-e")
        for n, d in enumerate((
            "AZUCAR CRUDO ZUKRA-E 1/2 KGS.",
            "SALSA DE SOYA KIKKOMAN 1/3.78L",
            "PAPA GAJO SAZONADA COUNTRY LW X9126 6/5LB",
        )):
            self.producto(description=d, internal_code=f"9-f{n}")
        self.assertEqual(svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False), [])

    def test_lo_ya_mapeado_no_vuelve_a_ofrecerse(self):
        a = self.producto(description="QUESO MOZZARELLA LEPRINO 1/15 LB", internal_code="9-g")
        b = self.producto(description="QUESO MOZZARELLA LEPRINO 1/2,27 KG", internal_code="9-h")
        svc.confirmar(self.db, catalog_product_id=b.id, nombre_nuevo="Mozzarella",
                      factor=Decimal(2270))
        self.assertEqual(svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False), [])

    def test_confirmar_muchos_crea_un_insumo_y_les_da_su_propio_factor(self):
        """Two pack sizes of one ingredient need two factors — a shared one
        would make one of them wrong by the ratio between the packs."""
        a = self.producto(description="QUESO LEPRINO 1/15 LB", internal_code="9-i")
        b = self.producto(description="QUESO LEPRINO 1/2,27 KG", internal_code="9-j")
        n = svc.confirmar_muchos(
            self.db,
            catalog_product_ids=[a.id, b.id],
            nombre_nuevo="Mozzarella rallado",
            unidad_base="g",
            factores={a.id: Decimal("6803.886"), b.id: Decimal(2270)},
        )
        self.assertEqual(n, 2)
        insumos = list(self.db.scalars(select(Insumo)))
        self.assertEqual(len(insumos), 1)          # UN insumo, no dos
        fuentes = {f.catalog_product_id: f for f in self.db.scalars(select(InsumoFuente))}
        self.assertEqual(len(fuentes), 2)
        self.assertEqual(fuentes[a.id].factor_conversion, Decimal("6803.8860"))
        self.assertEqual(fuentes[b.id].factor_conversion, Decimal("2270.0000"))
        # Non-negotiable #4 holds for every row the group wrote.
        for f in fuentes.values():
            self.assertEqual(f.status, "confirmed")
            self.assertEqual(f.mapped_by, "human")


class TestParecidosPorLLM(MapeoTestCase):
    """Claude proposes the grouping; `similitud` is the fallback.

    The vetoes are the point of these: a model that proposes a merge is
    still subject to the same over-merge rule as the string comparison, so
    the safety property does not depend on which proposer answered.
    """

    def _con_llm(self, veredicto):
        return mock.patch.object(svc.llm_insumo, "agrupar", return_value=veredicto)

    def test_usa_lo_que_propone_el_llm_aunque_el_texto_no_se_parezca(self):
        # 0.55 is under UMBRAL_SUGERIDO, so the similarity path would offer
        # this unticked. Claude saying "seguro" is what ticks it.
        a = self.producto(description="OSOBUCO", internal_code="l-a",
                          categoria_slug="alimentos_carnes")
        b = self.producto(description="OSOBUCO PICADO, CAJAS 3KG APROX (P/KG)",
                          internal_code="l-b", categoria_slug="alimentos_carnes")
        with self._con_llm({b.id: "seguro"}):
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual([p.producto.id for p in grupo], [b.id])
        self.assertEqual(grupo[0].origen, "llm")
        self.assertTrue(grupo[0].sugerido)

    def test_posible_se_ofrece_pero_no_llega_marcado(self):
        # A bulk confirm is what the checkbox feeds, so "probably" is not
        # enough to pre-approve one.
        a = self.producto(description="ALMENDRAS TAJA 100G", internal_code="l-c",
                          categoria_slug="alimentos_abarrotes")
        b = self.producto(description="ALMENDRAS FILETEADAS S/PIEL MFS 1/1KG",
                          internal_code="l-d", categoria_slug="alimentos_abarrotes")
        with self._con_llm({b.id: "posible"}):
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual([p.producto.id for p in grupo], [b.id])
        self.assertFalse(grupo[0].sugerido)

    def test_el_guardia_de_conflicto_veta_al_llm(self):
        # The over-merge rule does not move because the proposer changed.
        a = self.producto(description="PECHUGA CON HUESO", internal_code="l-e",
                          categoria_slug="alimentos_pollo")
        b = self.producto(description="PECHUGA DESHUESADA", internal_code="l-f",
                          categoria_slug="alimentos_pollo")
        with self._con_llm({b.id: "seguro"}) as espia:
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual(grupo, [])
        # Vetoed *before* the call, so it is never even offered as a candidate.
        self.assertEqual(espia.call_args.args[1], [])

    def test_la_familia_veta_al_llm(self):
        a = self.producto(description="PEPINOS GRANDE", internal_code="l-g",
                          categoria_slug="alimentos_verduras")
        b = self.producto(description="BOLSAS VIKINGO GRANDE", internal_code="l-h",
                          categoria_slug="empaques")
        with self._con_llm({b.id: "seguro"}):
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual(grupo, [])

    def test_sin_respuesta_del_llm_cae_a_similitud(self):
        a = self.producto(description="QUESO MOZZARELLA RALLADO LEPRINO 1/15 LB",
                          internal_code="l-i", categoria_slug="alimentos_lacteos")
        self.producto(description="QUESO MOZZARELLA RALLADO LEPRINO 1/2,27 KG",
                      internal_code="l-j", categoria_slug="alimentos_lacteos")
        with self._con_llm(None):
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual(len(grupo), 1)
        self.assertEqual(grupo[0].origen, "similitud")
        self.assertTrue(grupo[0].sugerido)

    def test_ninguno_es_una_respuesta_no_un_fallo(self):
        # `{}` is the model saying "none of these"; it must NOT fall back to
        # similitud, or the model's "no" would be silently overruled by the
        # method it replaced.
        a = self.producto(description="QUESO MOZZARELLA RALLADO LEPRINO 1/15 LB",
                          internal_code="l-k", categoria_slug="alimentos_lacteos")
        self.producto(description="QUESO MOZZARELLA RALLADO LEPRINO 1/2,27 KG",
                      internal_code="l-l", categoria_slug="alimentos_lacteos")
        with self._con_llm({}):
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        self.assertEqual(grupo, [])


class TestParseGrupo(unittest.TestCase):
    """`llm_insumo._parse_grupo` -- where an invented row gets dropped."""

    def test_lee_indices_y_certeza(self):
        self.assertEqual(llm_insumo._parse_grupo("1|seguro\n3|posible", 3),
                         {0: "seguro", 2: "posible"})

    def test_un_guion_es_ninguno_no_un_fallo(self):
        self.assertEqual(llm_insumo._parse_grupo("-", 5), {})

    def test_vacio_o_prosa_es_un_fallo(self):
        self.assertIsNone(llm_insumo._parse_grupo("", 5))
        self.assertIsNone(llm_insumo._parse_grupo("No encontré ninguno igual.", 5))

    def test_descarta_un_indice_que_no_existe(self):
        # The same rule as a slug that was never offered: invented output is
        # dropped, never resolved to whatever it happens to land on.
        self.assertIsNone(llm_insumo._parse_grupo("9|seguro", 3))
        self.assertEqual(llm_insumo._parse_grupo("9|seguro\n2|seguro", 3), {1: "seguro"})

    def test_descarta_una_certeza_inventada(self):
        self.assertIsNone(llm_insumo._parse_grupo("1|quizas", 3))


class TestPosicionTrasDecidir(MapeoTestCase):
    """Where the cursor lands after a confirmation.

    The old behaviour reset to the first card, which never skipped anything
    but threw away the operator's place 300 rows into a queue. These are
    about keeping the place *without* reintroducing the skip.
    """

    def _cola(self, *nombres):
        # Purchase count decides the order, so the counts here ARE the order.
        for i, nombre in enumerate(nombres):
            p = self.producto(description=nombre, internal_code=f"p-{i}",
                              categoria_slug="alimentos_carnes")
            self.compras(p, len(nombres) - i)
        return [p.description for p, _ in svc.pendientes(self.db, limite=50)]

    def test_el_cursor_no_se_mueve_al_confirmar_el_de_la_pagina(self):
        # The decided product is AT the cursor, so its own departure pulls
        # the next one into that exact slot. Advancing would skip it.
        orden = self._cola("A", "B", "C", "D", "E")
        self.assertEqual(orden, ["A", "B", "C", "D", "E"])
        d = svc.pendientes(self.db, limite=1, desde=3)[0][0]
        self.assertEqual(d.description, "D")
        self.assertEqual(
            svc.posicion_tras_decidir(self.db, desde=3, decididos={d.id}), 3
        )

    def test_un_parecido_por_detras_corre_el_cursor_hacia_atras(self):
        # This is the case the old reset-to-zero was really guarding against:
        # confirming D together with B, which sits behind the cursor, shifts
        # everything down by one more. Keeping desde=3 would land on F and
        # silently skip E.
        self._cola("A", "B", "C", "D", "E", "F")
        ids = {p.description: p.id for p, _ in svc.pendientes(self.db, limite=50)}
        self.assertEqual(
            svc.posicion_tras_decidir(
                self.db, desde=3, decididos={ids["D"], ids["B"]}
            ),
            2,
        )

    def test_un_parecido_por_delante_no_mueve_el_cursor(self):
        self._cola("A", "B", "C", "D", "E", "F")
        ids = {p.description: p.id for p, _ in svc.pendientes(self.db, limite=50)}
        self.assertEqual(
            svc.posicion_tras_decidir(
                self.db, desde=3, decididos={ids["D"], ids["F"]}
            ),
            3,
        )

    def test_en_la_primera_pagina_no_hay_nada_que_correr(self):
        self._cola("A", "B")
        ids = {p.description: p.id for p, _ in svc.pendientes(self.db, limite=50)}
        self.assertEqual(
            svc.posicion_tras_decidir(self.db, desde=0, decididos={ids["A"]}), 0
        )


class TestCobertura(MapeoTestCase):
    """Rows are the wrong denominator: «0 de 1 435» reads as hopeless and is
    the number that matters least. Purchases are steeply concentrated."""

    def test_mide_compras_y_no_filas(self):
        mucho = self.producto(description="ARROZ", internal_code="8-arr")
        self.producto(description="AZAFRAN", internal_code="8-aza")
        self.compras(mucho, 9)
        self.compras(self.db.scalar(select(CatalogProduct).where(CatalogProduct.internal_code == "8-aza")), 1)

        self.assertEqual(svc.cobertura(self.db)["pct"], 0)
        svc.confirmar(self.db, catalog_product_id=mucho.id, nombre_nuevo="Arroz",
                      factor=Decimal(1000))
        # One product of two, but nine purchases of ten.
        self.assertEqual(svc.cobertura(self.db)["pct"], 90.0)


class TestPrecomputo(MapeoTestCase):
    """The grouping pass, and the cache the queue reads instead of Claude.

    One call per anchor, the same `llm_insumo.agrupar` the live path uses,
    so a cached row and a fresh one come out of the same function. The
    property that matters most: a precomputed anchor with no pairs must read
    as "nothing looks like this", NOT as "nobody has looked" -- otherwise
    the queue quietly pays for a live call on every card forever.
    """

    def _tres(self):
        a = self.producto(description="OSOBUCO", internal_code="pc-a",
                          categoria_slug="alimentos_carnes")
        b = self.producto(description="OSOBUCO PICADO", internal_code="pc-b",
                          categoria_slug="alimentos_carnes")
        c = self.producto(description="CULANTRO COYOTE", internal_code="pc-c",
                          categoria_slug="alimentos_verduras")
        return a, b, c

    def _responde(self, fn):
        return mock.patch.object(svc.llm_insumo, "agrupar", side_effect=fn)

    def test_una_llamada_por_ancla(self):
        self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}) as llamadas:
            informe = svc.precomputar_parecidos(self.db)
        self.assertEqual(llamadas.call_count, 3)
        self.assertEqual(informe["anchors"], 3)

    def test_guarda_los_pares_en_ambos_sentidos(self):
        a, b, _c = self._tres()

        def responde(descripcion, candidatos):
            # Only OSOBUCO's own pass finds anything; the row for B is
            # written from A's pass, which is what backfills B.
            if descripcion == "OSOBUCO":
                return {b.id: "posible"}
            return {}

        with self._responde(responde):
            svc.precomputar_parecidos(self.db)

        pares = {
            (p.anchor_id, p.miembro_id)
            for p in self.db.scalars(select(CatalogParecido))
        }
        self.assertEqual(pares, {(a.id, b.id), (b.id, a.id)})

    def test_un_producto_nuevo_se_backfillea_en_las_anclas_viejas(self):
        """The reason one call per new product is enough.

        A and C are grouped first, finding nothing. B arrives later; its own
        pass finds A, and because rows go both ways, A's card offers B
        without A ever being recomputed.
        """
        a = self.producto(description="OSOBUCO", internal_code="bf-a",
                          categoria_slug="alimentos_carnes")
        c = self.producto(description="CULANTRO COYOTE", internal_code="bf-c",
                          categoria_slug="alimentos_verduras")
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}):
            svc.precomputar_parecidos(self.db)
        self.assertEqual(svc.parecidos(self.db, a, solo_receta=False,
                                       solo_reciente=False), [])

        b = self.producto(description="OSOBUCO PICADO", internal_code="bf-b",
                          categoria_slug="alimentos_carnes")
        with mock.patch.object(
            svc.llm_insumo, "agrupar", return_value={a.id: "seguro"}
        ) as llamadas:
            svc.precomputar_parecidos(self.db)
        # Only the new product cost a call.
        self.assertEqual(llamadas.call_count, 1)

        # And A now offers B, with no call and no recompute of A.
        with mock.patch.object(svc.llm_insumo, "agrupar") as vivo:
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        vivo.assert_not_called()
        self.assertEqual([p.producto.id for p in grupo], [b.id])
        self.assertTrue(grupo[0].sugerido)
        del c

    def test_un_ancla_sin_parecidos_queda_marcada_igual(self):
        _a, _b, c = self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}):
            svc.precomputar_parecidos(self.db)
        self.assertIsNotNone(self.db.get(CatalogAgrupacion, c.id))

    def test_la_cola_lee_el_cache_sin_llamar_al_modelo(self):
        a, b, _c = self._tres()

        def responde(descripcion, candidatos):
            return {b.id: "posible"} if descripcion == "OSOBUCO" else {}

        with self._responde(responde):
            svc.precomputar_parecidos(self.db)

        with mock.patch.object(svc.llm_insumo, "agrupar") as vivo:
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        vivo.assert_not_called()
        self.assertEqual([p.producto.id for p in grupo], [b.id])
        self.assertEqual(grupo[0].origen, "llm")
        self.assertFalse(grupo[0].sugerido)  # "posible" does not pre-tick

    def test_un_ancla_marcada_y_vacia_no_gasta_una_llamada(self):
        _a, _b, c = self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}):
            svc.precomputar_parecidos(self.db)
        with mock.patch.object(svc.llm_insumo, "agrupar") as vivo:
            grupo = svc.parecidos(self.db, c, solo_receta=False, solo_reciente=False)
        vivo.assert_not_called()
        self.assertEqual(grupo, [])

    def test_un_producto_sin_precomputar_cae_a_la_via_viva(self):
        a, b, _c = self._tres()
        with mock.patch.object(
            svc.llm_insumo, "agrupar", return_value={b.id: "seguro"}
        ) as vivo:
            grupo = svc.parecidos(self.db, a, solo_receta=False, solo_reciente=False)
        vivo.assert_called_once()
        self.assertEqual([p.producto.id for p in grupo], [b.id])

    def test_un_ancla_que_falla_no_queda_marcada(self):
        # "We could not look" must not be stored as "nothing looks like
        # this", or a network blip becomes a permanent wrong answer.
        self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value=None):
            informe = svc.precomputar_parecidos(self.db)
        self.assertEqual(informe["anchors"], 0)
        self.assertEqual(informe["fallidos"], 3)
        self.assertEqual(self.db.scalars(select(CatalogAgrupacion)).all(), [])

    def test_una_segunda_corrida_no_repite_trabajo(self):
        self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}):
            svc.precomputar_parecidos(self.db)
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}) as llamadas:
            informe = svc.precomputar_parecidos(self.db)
        llamadas.assert_not_called()
        self.assertEqual(informe["anchors"], 0)

    def test_el_limite_corta_y_deja_el_resto(self):
        # What makes 405 anchors survivable: a bounded slice, resumable.
        self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}) as llamadas:
            informe = svc.precomputar_parecidos(self.db, limite=2)
        self.assertEqual(llamadas.call_count, 2)
        self.assertEqual(informe["anchors"], 2)
        self.assertEqual(informe["restantes"], 1)
        self.assertEqual(svc.faltan_por_agrupar(self.db), 1)

        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}) as llamadas:
            informe = svc.precomputar_parecidos(self.db, limite=40)
        self.assertEqual(llamadas.call_count, 1)  # picks up where it stopped
        self.assertEqual(svc.faltan_por_agrupar(self.db), 0)

    def test_lo_hecho_sobrevive_una_interrupcion(self):
        # Commits per anchor, so a pass that dies partway keeps what it
        # earned -- which is the only reason a 405-call pass is tolerable.
        self._tres()
        n = {"i": 0}

        def falla_en_el_segundo(descripcion, candidatos):
            n["i"] += 1
            if n["i"] > 1:
                raise RuntimeError("se cayó la red")
            return {}

        with mock.patch.object(
            svc.llm_insumo, "agrupar", side_effect=falla_en_el_segundo
        ):
            with self.assertRaises(RuntimeError):
                svc.precomputar_parecidos(self.db)

        self.assertEqual(len(self.db.scalars(select(CatalogAgrupacion)).all()), 1)
        self.assertEqual(svc.faltan_por_agrupar(self.db), 2)

    def test_confirmar_borra_el_cache_del_producto(self):
        a, b, _c = self._tres()

        def responde(descripcion, candidatos):
            return {b.id: "posible"} if descripcion == "OSOBUCO" else {}

        with self._responde(responde):
            svc.precomputar_parecidos(self.db)

        svc.confirmar(self.db, catalog_product_id=a.id, nombre_nuevo="Osobuco",
                      unidad_base="g", factor=Decimal(1000))

        # Gone in BOTH directions: the other osobuco must not still be
        # offered this one, and this one must not be offered at all.
        restantes = {
            (p.anchor_id, p.miembro_id)
            for p in self.db.scalars(select(CatalogParecido))
        }
        self.assertEqual(restantes, set())
        self.assertIsNone(self.db.get(CatalogAgrupacion, a.id))

    def test_descartar_borra_el_cache(self):
        self._tres()
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}):
            svc.precomputar_parecidos(self.db)
        svc.descartar_filtro(self.db, solo_receta=False, solo_reciente=False)
        self.assertEqual(self.db.scalars(select(CatalogParecido)).all(), [])
        self.assertEqual(self.db.scalars(select(CatalogAgrupacion)).all(), [])

    def test_solo_agrupa_las_familias_que_entran_en_una_receta(self):
        self.producto(description="CLORO", internal_code="pc-x",
                      categoria_slug="limpieza")
        self.producto(description="ESCOBA", internal_code="pc-y",
                      categoria_slug="menaje")
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}) as llamadas:
            informe = svc.precomputar_parecidos(self.db)
        self.assertEqual(informe["anchors"], 0)
        llamadas.assert_not_called()

    def test_no_agrupa_lo_comprado_hace_mucho(self):
        # Scope is the queue, not every pending product in those families.
        viejo = dt.date.today() - dt.timedelta(days=400)
        self.producto(description="OSOBUCO", internal_code="vj-a",
                      categoria_slug="alimentos_carnes", last_purchase_at=viejo)
        self.producto(description="OSOBUCO PICADO", internal_code="vj-b",
                      categoria_slug="alimentos_carnes", last_purchase_at=viejo)
        with mock.patch.object(svc.llm_insumo, "agrupar", return_value={}) as llamadas:
            informe = svc.precomputar_parecidos(self.db)
        self.assertEqual(informe["anchors"], 0)
        llamadas.assert_not_called()


class TestCostoPreview(MapeoTestCase):
    """The cost card under the fields, computed the way costing computes.

    The point of these is that the preview cannot drift from the engine:
    it runs `costing.costo_unitario` over the real price points, so a card
    that agreed with `/insumos/{id}` only by coincidence is impossible.
    """

    def _con_compras(self, precios):
        p = self.producto(description="AZUCAR", categoria_slug="alimentos_abarrotes")
        for i, precio in enumerate(precios):
            self.db.add(CatalogPricePoint(
                catalog_product_id=p.id,
                clave=f"{p.id:03d}" + "0" * 44 + f"{i:03d}",
                numero_linea=i, fecha=dt.date.today(),
                cantidad=Decimal(1),
                precio_efectivo=Decimal(precio),
                precio_efectivo_crc=Decimal(precio),
                precio_efectivo_normalizado_crc=Decimal(precio),
            ))
        self.db.commit()
        return p

    def test_divide_por_el_factor(self):
        p = self._con_compras([1000])
        vista = svc.costo_preview(self.db, p, factor=Decimal(1000), unidad_base="g")
        self.assertEqual(vista.valor, Decimal("1.0000"))

    def test_sin_factor_no_hay_costo_y_nunca_se_asume_uno(self):
        # Non-negotiable #7, and the most expensive mistake on this screen:
        # a factor of 1 costs per package instead of per gram and lands on
        # a number that looks perfectly plausible.
        p = self._con_compras([1000])
        vista = svc.costo_preview(self.db, p, factor=None, unidad_base="g")
        self.assertIsNone(vista.valor)
        self.assertIsNone(vista.factor)

    def test_el_metodo_cambia_el_numero(self):
        p = self._con_compras([100, 200, 900])
        def valor(metodo):
            return svc.costo_preview(
                self.db, p, factor=Decimal(1), unidad_base="g", metodo=metodo
            ).valor
        self.assertEqual(valor("median_window"), Decimal("200.0000"))
        self.assertEqual(valor("max_window"), Decimal("900.0000"))
        self.assertEqual(valor("min_window"), Decimal("100.0000"))

    def test_la_merma_divide_no_multiplica(self):
        # Cost is spread over what SURVIVES, so merma raises it.
        p = self._con_compras([100])
        vista = svc.costo_preview(
            self.db, p, factor=Decimal(1), unidad_base="g", merma_pct=Decimal(20)
        )
        self.assertEqual(vista.valor, Decimal("125.0000"))

    def test_cuenta_las_compras_sin_tipo_de_cambio_en_vez_de_ocultarlas(self):
        p = self._con_compras([100])
        self.db.add(CatalogPricePoint(
            catalog_product_id=p.id, clave="x" * 47, numero_linea=99,
            fecha=dt.date.today(), cantidad=Decimal(1),
            precio_efectivo=Decimal(100), precio_efectivo_normalizado_crc=None,
        ))
        self.db.commit()
        vista = svc.costo_preview(self.db, p, factor=Decimal(1), unidad_base="g")
        self.assertEqual(vista.descartadas, 1)
        self.assertEqual(vista.observaciones, 1)

    def test_coincide_con_el_motor_de_costeo(self):
        # The whole reason it reuses `costo_unitario` rather than doing its
        # own arithmetic.
        from app.services import costing
        p = self._con_compras([100, 200, 900])
        serie = [
            costing.Observation(
                fecha=dt.date.today(),
                precio_por_unidad_base=Decimal(v) / Decimal(4),
                cantidad_unidad_base=Decimal(4),
            )
            for v in (100, 200, 900)
        ]
        esperado = costing.costo_unitario(serie, merma_pct=Decimal(10))
        vista = svc.costo_preview(
            self.db, p, factor=Decimal(4), unidad_base="g", merma_pct=Decimal(10)
        )
        self.assertEqual(vista.valor, esperado.valor)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
