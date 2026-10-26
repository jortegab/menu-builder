"""Búsqueda global (DESIGN.md §3.8).

What this app genuinely lacked was search at all -- there was no way to
find an insumo except by paging `/insumos`, and that gets worse with every
mapping confirmed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import busqueda as svc  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import recetas as recetas_svc  # noqa: E402


class Busqueda(DbTestCase):
    def setUp(self):
        super().setUp()
        self.insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        self.plato = recetas_svc.create(
            self.db, nombre="Pechuga a la plancha", tipo="plato", porciones="1",
            categoria_menu="Fondos",
        )
        self.salsa = recetas_svc.create(
            self.db, nombre="Salsa de pechuga", tipo="preparacion",
            rendimiento_cantidad="1000", rendimiento_unidad="g",
        )

    def test_encuentra_las_tres_cosas_por_nombre(self):
        tipos = [h.tipo for h in svc.buscar(self.db, "pechuga")]
        self.assertEqual(tipos, ["Plato", "Preparación", "Insumo"])

    def test_el_orden_es_fijo_y_no_por_puntaje(self):
        """A scored blend would reorder itself between keystrokes, which is
        the one thing a list you are about to press Enter on must not do."""
        primero = svc.buscar(self.db, "pechuga")[0]
        self.assertEqual(primero.tipo, "Plato")
        self.assertEqual(primero.url, f"/recetas/{self.plato.id}")

    def test_una_consulta_vacia_no_devuelve_el_catalogo_entero(self):
        self.assertEqual(svc.buscar(self.db, ""), [])
        self.assertEqual(svc.buscar(self.db, "   "), [])

    def test_no_distingue_mayusculas(self):
        self.assertTrue(svc.buscar(self.db, "PECHUGA"))
        self.assertTrue(svc.buscar(self.db, "pEcHuGa"))

    def test_lo_archivado_aparece_y_se_marca(self):
        """«¿Dónde se fue ese plato?» is exactly what search gets asked, and
        answering "no existe" when it is merely archived sends the operator
        looking in the wrong place."""
        recetas_svc.set_activo(self.db, self.plato, False)
        hit = next(h for h in svc.buscar(self.db, "pechuga") if h.tipo == "Plato")
        self.assertEqual(hit.detalle, "Fondos")

        insumos_svc.set_activo(self.db, self.insumo, False)
        hit_i = next(h for h in svc.buscar(self.db, "pechuga") if h.tipo == "Insumo")
        self.assertEqual(hit_i.detalle, "archivado")

    def test_se_corta_en_una_pantalla(self):
        for n in range(12):
            insumos_svc.create(self.db, nombre=f"Pechuga {n}", unidad_base="g")
        self.assertEqual(len(svc.buscar(self.db, "pechuga")), svc.LIMITE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
