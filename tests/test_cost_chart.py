"""`services/cost_chart.py` -- the histórico card's arithmetic and SVG.

Pure, so this needs no database. Weighted toward the cases that would
render a *misleading* card rather than a broken one: a flat series that
would divide by zero, a base of zero that would make a percentage a lie,
and the difference between "did not move" and "nothing to compare".
"""

from __future__ import annotations

import datetime as dt
import unittest
from decimal import Decimal

from app.services.cost_chart import Punto, estadisticas, svg, variacion

HOY = dt.date(2026, 9, 2)


def punto(dias_atras: int, valor: str, estimado: bool = False) -> Punto:
    return Punto(HOY - dt.timedelta(days=dias_atras), Decimal(valor), estimado)


class Estadisticas(unittest.TestCase):
    def test_serie_vacia_no_tiene_estadisticas(self):
        self.assertIsNone(estadisticas([]))

    def test_las_seis_cifras(self):
        s = estadisticas([Decimal(x) for x in ("10", "20", "20", "50")])
        self.assertEqual(s["mean"], Decimal(25))
        self.assertEqual(s["median"], Decimal(20))
        self.assertEqual(s["mode"], Decimal(20))
        self.assertEqual(s["max"], Decimal(50))
        self.assertEqual(s["min"], Decimal(10))
        self.assertGreater(s["std"], 0)

    def test_un_solo_punto_tiene_estadisticas_y_desviacion_cero(self):
        s = estadisticas([Decimal("7.5")])
        self.assertEqual(s["mean"], Decimal("7.5"))
        self.assertEqual(s["std"], 0)


class Variacion(unittest.TestCase):
    def test_menos_de_dos_puntos_no_es_comparable(self):
        self.assertIsNone(variacion([]))
        self.assertIsNone(variacion([Decimal(10)]))

    def test_sube(self):
        self.assertEqual(variacion([Decimal(100), Decimal(108)]), Decimal(8))

    def test_baja(self):
        self.assertEqual(variacion([Decimal(100), Decimal(90)]), Decimal(-10))

    def test_cero_es_una_respuesta_real_distinta_de_none(self):
        """"No se movió" y "no hay con qué comparar" son cosas distintas, y
        la tarjeta las dibuja distinto."""
        self.assertEqual(variacion([Decimal(100), Decimal(100)]), Decimal(0))

    def test_compara_los_dos_ultimos_no_el_primero_con_el_ultimo(self):
        self.assertEqual(
            variacion([Decimal(10), Decimal(100), Decimal(110)]), Decimal(10)
        )

    def test_base_cero_no_produce_un_porcentaje(self):
        """Una receta cuyos ingredientes perdieron el precio guarda cero
        legítimamente; un porcentaje contra esa base es una división por
        cero o una mentira con el signo cambiado."""
        self.assertIsNone(variacion([Decimal(0), Decimal(50)]))


class Svg(unittest.TestCase):
    def test_un_punto_no_dibuja_linea(self):
        self.assertIsNone(svg([punto(0, "100")]))

    def test_dos_puntos_dibujan(self):
        salida = svg([punto(30, "100"), punto(0, "120")])
        self.assertIn("<svg", salida)
        self.assertIn("polyline", salida)
        self.assertIn("₡120.00", salida)
        self.assertIn("₡100.00", salida)

    def test_una_serie_plana_no_divide_entre_cero(self):
        salida = svg([punto(30, "100"), punto(0, "100")])
        self.assertIn("<svg", salida)

    def test_dos_puntos_del_mismo_dia_no_dividen_entre_cero(self):
        salida = svg([punto(0, "100"), punto(0, "120")])
        self.assertIn("<svg", salida)

    def test_los_puntos_estimados_se_marcan(self):
        salida = svg([punto(30, "100", True), punto(0, "120")])
        self.assertIn("cost-chart__pt--estimado", salida)
        self.assertIn("es-estimado", salida)
        self.assertIn("(estimado)", salida)

    def test_un_tramo_entre_dos_reales_no_se_marca(self):
        salida = svg([punto(60, "90"), punto(30, "100"), punto(0, "120")])
        self.assertNotIn("es-estimado", salida)

    def test_el_tramo_que_toca_un_estimado_se_marca(self):
        """La unión entre lo real y lo sembrado tampoco es una observación,
        así que se dibuja punteada."""
        salida = svg([punto(60, "90", True), punto(30, "100"), punto(0, "120")])
        self.assertEqual(salida.count("es-estimado"), 1)

    def test_ordena_por_fecha_aunque_lleguen_al_revés(self):
        salida = svg([punto(0, "120"), punto(30, "100")])
        i_viejo = salida.index((HOY - dt.timedelta(days=30)).strftime("%d/%m/%Y"))
        i_nuevo = salida.rindex(HOY.strftime("%d/%m/%Y"))
        self.assertLess(i_viejo, i_nuevo)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
