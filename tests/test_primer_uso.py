"""El primer arranque (DESIGN.md §3.10).

Four steps, every one of them evaluated from data the app already has --
which is the design constraint, not a convenience. A checklist with its own
progress column would be a second source of truth about whether the catalog
has been synced, one that can disagree with `catalog_sync_run`.
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

from app.models import CatalogProduct, CatalogSyncRun, InsumoFuente  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import primer_uso as svc  # noqa: E402
from app.services import recetas as recetas_svc  # noqa: E402


class PrimerUso(DbTestCase):
    def hechos(self):
        return {p.clave: p.hecho for p in svc.pasos(self.db)}

    def test_una_instalacion_vacia_no_tiene_ningun_paso_hecho(self):
        self.assertEqual(
            self.hechos(), {"sync": False, "mapeo": False, "insumo": False, "receta": False}
        )

    def test_un_sync_fallido_no_cuenta_como_sincronizado(self):
        """A sync that failed left the mirror exactly as it was, so telling
        the operator that step is done would be a lie the next step then
        contradicts."""
        self.db.add(CatalogSyncRun(status="error", error="boom"))
        self.db.commit()
        self.assertFalse(self.hechos()["sync"])

        self.db.add(CatalogSyncRun(status="ok"))
        self.db.commit()
        self.assertTrue(self.hechos()["sync"])

    def test_una_sugerencia_no_adelanta_el_mapeo(self):
        """Nothing is active until a person confirms it (non-negotiable #4),
        so a suggestion sitting in the queue has advanced nothing."""
        insumo = insumos_svc.create(self.db, nombre="Pechuga", unidad_base="g")
        producto = CatalogProduct(
            internal_code="1-1", upstream_product_id=1, supplier_id=1,
            supplier_nombre="Mayca", description="PECHUGA 1/5KG",
        )
        self.db.add(producto)
        self.db.flush()
        fuente = InsumoFuente(
            insumo_id=insumo.id, catalog_product_id=producto.id,
            factor_conversion=Decimal(5000), status="suggested", mapped_by="llm",
        )
        self.db.add(fuente)
        self.db.commit()
        self.assertFalse(self.hechos()["mapeo"])

        fuente.status = "confirmed"
        fuente.mapped_by = "human"
        self.db.commit()
        self.assertTrue(self.hechos()["mapeo"])

    def test_la_tarjeta_desaparece_cuando_estan_los_cuatro(self):
        """Gone for good, not collapsed and not dismissible: a card that
        survives its own purpose is furniture."""
        self.assertIsNotNone(svc.mostrar(self.db))

        self.db.add(CatalogSyncRun(status="ok"))
        insumo = insumos_svc.create(self.db, nombre="Pechuga", unidad_base="g")
        producto = CatalogProduct(
            internal_code="1-1", upstream_product_id=1, supplier_id=1,
            supplier_nombre="Mayca", description="PECHUGA 1/5KG",
        )
        self.db.add(producto)
        self.db.flush()
        self.db.add(
            InsumoFuente(
                insumo_id=insumo.id, catalog_product_id=producto.id,
                factor_conversion=Decimal(5000), status="confirmed", mapped_by="human",
            )
        )
        self.db.commit()
        self.assertIsNotNone(svc.mostrar(self.db))   # falta la receta

        recetas_svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        self.assertIsNone(svc.mostrar(self.db))

    def test_los_pasos_van_en_orden_y_cada_uno_lleva_a_donde_hacerlo(self):
        pasos = svc.pasos(self.db)
        self.assertEqual(
            [p.clave for p in pasos], ["sync", "mapeo", "insumo", "receta"]
        )
        self.assertEqual(pasos[0].url, "/ajustes")
        self.assertEqual(pasos[1].url, "/insumos/mapeo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
