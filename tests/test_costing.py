import datetime as dt
import unittest
from decimal import Decimal

from app.services.costing import (
    Observation,
    apply_merma,
    costo_unitario,
    price_from_series,
)

HOY = dt.date(2026, 9, 2)


def obs(dias_atras: int, precio: str, cantidad: str = "1") -> Observation:
    return Observation(
        fecha=HOY - dt.timedelta(days=dias_atras),
        precio_por_unidad_uso=Decimal(precio),
        cantidad_unidad_uso=Decimal(cantidad),
    )


class TestEmptySeries(unittest.TestCase):
    def test_no_observations_is_no_cost_never_zero(self):
        # Non-negotiable #7. A zero would make the ingredient we know least
        # about look like the cheapest thing on the menu.
        r = price_from_series([], "avg_window", 90, HOY)
        self.assertIsNone(r.valor)
        self.assertTrue(r.sin_precio)
        self.assertEqual(r.observaciones_usadas, 0)


class TestMethods(unittest.TestCase):
    def setUp(self):
        self.serie = [
            obs(80, "4.00", "10"),
            obs(40, "5.00", "30"),
            obs(10, "6.00", "10"),
        ]

    def test_last_is_the_newest_across_suppliers(self):
        r = price_from_series(self.serie, "last", 90, HOY)
        self.assertEqual(r.valor, Decimal("6.00"))
        self.assertEqual(r.antiguedad_dias, 10)

    def test_last_ignores_the_window(self):
        # `last` answers "what would this cost me to cook today", so an
        # observation older than the window is still the answer.
        r = price_from_series([obs(400, "9.00")], "last", 90, HOY)
        self.assertEqual(r.valor, Decimal("9.00"))
        self.assertFalse(r.precio_viejo)

    def test_avg_window(self):
        r = price_from_series(self.serie, "avg_window", 90, HOY)
        self.assertEqual(r.valor, Decimal(5))
        self.assertEqual(r.observaciones_usadas, 3)

    def test_weighted_avg_weights_by_quantity(self):
        # (4*10 + 5*30 + 6*10) / 50 = 250/50 = 5.00 -- here equal to the
        # plain mean by construction; the next test proves it differs.
        r = price_from_series(self.serie, "weighted_avg_window", 90, HOY)
        self.assertEqual(r.valor, Decimal(5))

    def test_weighted_avg_differs_from_plain_mean(self):
        serie = [obs(10, "10.00", "1"), obs(5, "20.00", "99")]
        plain = price_from_series(serie, "avg_window", 90, HOY).valor
        weighted = price_from_series(serie, "weighted_avg_window", 90, HOY).valor
        self.assertEqual(plain, Decimal(15))
        self.assertGreater(weighted, Decimal("19.8"))

    def test_weighted_avg_degrades_to_mean_on_zero_weights(self):
        serie = [obs(10, "10.00", "0"), obs(5, "20.00", "0")]
        r = price_from_series(serie, "weighted_avg_window", 90, HOY)
        self.assertEqual(r.valor, Decimal(15))

    def test_median_max_min(self):
        self.assertEqual(price_from_series(self.serie, "median_window", 90, HOY).valor, Decimal(5))
        self.assertEqual(price_from_series(self.serie, "max_window", 90, HOY).valor, Decimal(6))
        self.assertEqual(price_from_series(self.serie, "min_window", 90, HOY).valor, Decimal(4))

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            price_from_series(self.serie, "mediana", 90, HOY)


class TestWindow(unittest.TestCase):
    def test_window_excludes_older_observations(self):
        serie = [obs(200, "100.00"), obs(10, "5.00")]
        r = price_from_series(serie, "avg_window", 90, HOY)
        self.assertEqual(r.valor, Decimal(5))
        self.assertEqual(r.observaciones_usadas, 1)

    def test_boundary_observation_is_inside_the_window(self):
        r = price_from_series([obs(90, "7.00")], "avg_window", 90, HOY)
        self.assertEqual(r.valor, Decimal(7))
        self.assertFalse(r.precio_viejo)

    def test_empty_window_falls_back_and_flags_it(self):
        # D12: silence would hide a delisted ingredient; refusal would break
        # a whole menu over one seasonal item.
        r = price_from_series([obs(200, "8.00"), obs(300, "3.00")], "avg_window", 90, HOY)
        self.assertEqual(r.valor, Decimal(8))
        self.assertTrue(r.precio_viejo)
        self.assertEqual(r.antiguedad_dias, 200)


class TestMerma(unittest.TestCase):
    def test_merma_divides_over_what_survives(self):
        # ₡4 000/kg at 12% merma is ₡4 545.45 per usable kg.
        r = costo_unitario([obs(1, "4000")], Decimal(12), "last", 90, HOY)
        self.assertEqual(r.valor, Decimal("4545.4545"))
        self.assertEqual(r.merma_pct, Decimal(12))

    def test_zero_merma_is_unchanged_but_recorded(self):
        r = costo_unitario([obs(1, "4000")], Decimal(0), "last", 90, HOY)
        self.assertEqual(r.valor, Decimal(4000))
        self.assertEqual(r.merma_pct, Decimal(0))

    def test_merma_on_no_price_is_still_no_price(self):
        r = costo_unitario([], Decimal(20), "avg_window", 90, HOY)
        self.assertIsNone(r.valor)

    def test_merma_out_of_range_rejected(self):
        base = price_from_series([obs(1, "100")], "last", 90, HOY)
        for bad in (Decimal(-1), Decimal(95), Decimal(99)):
            with self.assertRaises(ValueError, msg=str(bad)):
                apply_merma(base, bad)

    def test_merma_just_under_the_cap_is_allowed(self):
        base = price_from_series([obs(1, "100")], "last", 90, HOY)
        self.assertEqual(apply_merma(base, Decimal("94.9")).valor, Decimal("1960.7843"))

    def test_audit_fields_survive_merma(self):
        r = costo_unitario([obs(200, "10")], Decimal(10), "avg_window", 90, HOY)
        self.assertTrue(r.precio_viejo)
        self.assertEqual(r.antiguedad_dias, 200)
        self.assertEqual(r.metodo, "avg_window")
        self.assertEqual(r.ventana_dias, 90)


if __name__ == "__main__":
    unittest.main()
