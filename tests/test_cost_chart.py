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

from app.services.cost_chart import (
    Punto,
    composicion,
    estadisticas,
    spark,
    svg,
    variacion,
    variacion_ventana,
)

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


class ComposicionTest(unittest.TestCase):
    """DESIGN.md §3.3 -- the only expression of cost share on the builder."""

    def test_reparte_cien_por_ciento_en_orden_de_receta(self):
        segs = composicion(
            [
                ("Pechuga deshuesada", Decimal("784.80")),
                ("Papa", Decimal("213.00")),
                ("Salsa criolla", Decimal("131.85")),
                ("Aceite de oliva", Decimal("95.00")),
                ("Sal", Decimal("1.10")),
            ]
        )
        self.assertEqual([s.nombre for s in segs][0], "Pechuga deshuesada")
        self.assertEqual(segs[0].pct, Decimal("64.0"))
        self.assertEqual(segs[3].pct, Decimal("7.8"))
        # Rounding may land a tenth either side of 100; what must never
        # happen is a bar that is visibly short or overflows its track.
        self.assertLess(abs(sum(s.pct for s in segs) - 100), Decimal("0.5"))

    def test_el_orden_es_el_de_la_receta_no_el_del_tamano(self):
        segs = composicion(
            [("Sal", Decimal("1")), ("Pechuga", Decimal("99"))]
        )
        self.assertEqual([s.nombre for s in segs], ["Sal", "Pechuga"])

    def test_una_linea_sin_precio_no_aporta_segmento(self):
        # Non-negotiable #7: no usable price means NO cost, never zero. A
        # zero-width segment would claim the share is nothing, when it is
        # actually unknown -- and the receta is already flagged incompleta.
        segs = composicion(
            [("Pechuga", Decimal("100")), ("Aceite", None)]
        )
        self.assertEqual([s.nombre for s in segs], ["Pechuga"])
        self.assertEqual(segs[0].pct, Decimal("100.0"))

    def test_sin_nada_que_repartir_no_hay_barra(self):
        self.assertEqual(composicion([]), [])
        self.assertEqual(composicion([("Sal", None)]), [])
        self.assertEqual(composicion([("Sal", Decimal("0"))]), [])

    def test_los_colores_salen_de_la_paleta_de_upstream_y_ciclan(self):
        segs = composicion([(f"i{n}", Decimal("1")) for n in range(14)])
        self.assertEqual(segs[0].color, "--chart-cat-1")
        self.assertEqual(segs[11].color, "--chart-cat-12")
        self.assertEqual(segs[12].color, "--chart-cat-1")


class VariacionDeVentanaTests(unittest.TestCase):
    """§3.6's threshold is a WINDOW move, not a day-over-day one, and the
    difference is why `variacion_ventana` exists beside `variacion`."""

    def test_compara_el_primero_contra_el_ultimo(self):
        self.assertEqual(
            variacion_ventana([Decimal(100), Decimal(103), Decimal(110)]), Decimal(10)
        )

    def test_no_es_lo_mismo_que_variacion(self):
        # The history stores one point per day and rides the catalog sync,
        # so a plate that drifted 9% over the quarter reads as 0 day over
        # day. Using `variacion` for the board's threshold would hide every
        # sparkline on the screen, which is the failure that motivated a
        # second function rather than a parameter.
        serie = [Decimal(100), Decimal(109), Decimal(109)]
        self.assertEqual(variacion(serie), Decimal(0))
        self.assertEqual(variacion_ventana(serie), Decimal(9))

    def test_se_niega_igual_que_variacion(self):
        self.assertIsNone(variacion_ventana([]))
        self.assertIsNone(variacion_ventana([Decimal(10)]))
        self.assertIsNone(variacion_ventana([Decimal(0), Decimal(50)]))


class SparkTests(unittest.TestCase):
    def test_una_serie_plana_no_dibuja_nada(self):
        # The case that matters: today's real history is 27 identical
        # points per receta, and nine flat lines across a board are nine
        # pieces of ink saying nothing. The PRESENCE of a sparkline is the
        # signal (§3.6), so a flat one must be impossible for any caller
        # to produce, not merely discouraged by the board's threshold.
        self.assertIsNone(spark([Decimal(5)] * 27))

    def test_hacen_falta_dos_puntos(self):
        self.assertIsNone(spark([]))
        self.assertIsNone(spark([Decimal(5)]))

    def test_dibuja_una_polilinea_sin_ejes_ni_marcas(self):
        out = spark([Decimal(10), Decimal(20), Decimal(15)])
        self.assertIn("<polyline", out)
        self.assertIn('class="spark__line"', out)
        # A sparkline is a shape, not a chart: no axis labels, no markers.
        self.assertNotIn("<text", out)
        self.assertNotIn("<circle", out)

    def test_el_minimo_toca_el_piso_y_el_maximo_el_techo(self):
        out = spark([Decimal(10), Decimal(20)])
        self.assertIn("0.0,26.0", out)   # el mínimo, abajo
        self.assertIn("120.0,0.0", out)  # el máximo, arriba
