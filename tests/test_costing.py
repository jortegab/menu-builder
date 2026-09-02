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


class TestOutlierRobustness(unittest.TestCase):
    """Pins D17 -- why `median_window` is the default and not an average.

    The series is real: PEREJIL from Inversiones Hevar V&R, whose 90-day
    window holds prices between ₡850 and ₡1 190 plus one line at ₡120.79.
    That line is a bad invoice, not a price move, and 6% of the products
    with enough history carry one like it.
    """

    SERIE = [
        obs(75, "1190.0990"), obs(72, "1190.0990"), obs(68, "1190.0990"),
        obs(65, "120.7921"),  # <- the bad line
        obs(61, "1019.8020"), obs(58, "850.4951"), obs(56, "1190.0990"),
        obs(54, "1190.0000"), obs(51, "850.0000"), obs(48, "1190.0000"),
        obs(44, "850.0000"), obs(41, "850.0000"), obs(37, "850.0000"),
        obs(33, "1120.0000"),
    ]

    def test_median_ignores_the_bad_line(self):
        r = price_from_series(self.SERIE, "median_window", 90, HOY)
        self.assertGreater(r.valor, Decimal(1000))

    def test_mean_absorbs_it(self):
        media = price_from_series(self.SERIE, "avg_window", 90, HOY).valor
        mediana = price_from_series(self.SERIE, "median_window", 90, HOY).valor
        self.assertLess(media, mediana)

    def test_the_default_is_the_robust_one(self):
        from app.services.costing import DEFAULT_METHOD, DEFAULT_WINDOW_DAYS

        self.assertEqual(DEFAULT_METHOD, "median_window")
        self.assertEqual(DEFAULT_WINDOW_DAYS, 90)

    def test_median_equals_mean_on_two_observations(self):
        # Why the robustness is free: for the 54% of products with <=2
        # purchases in a window, every method agrees anyway.
        serie = [obs(30, "100"), obs(10, "200")]
        self.assertEqual(
            price_from_series(serie, "median_window", 90, HOY).valor,
            price_from_series(serie, "avg_window", 90, HOY).valor,
        )


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
