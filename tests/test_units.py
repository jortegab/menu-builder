import unittest
from decimal import Decimal

from app.services.units import (
    IncompatibleUnits,
    UnknownUnit,
    compatible_units,
    convert,
    dimension_of,
    normalize_unit,
    to_canonical,
)


class TestNormalize(unittest.TestCase):
    def test_aliases_fold(self):
        for raw, expected in [
            ("Kg", "kg"), ("KILOS", "kg"), ("grs", "g"), ("Gr", "g"),
            ("lt", "l"), ("Litros", "l"), ("und", "unidad"), ("u", "unidad"),
        ]:
            self.assertEqual(normalize_unit(raw), expected, raw)

    def test_unknown_unit_raises_rather_than_defaulting(self):
        # Defaulting an unrecognized unit to `unidad` would cost the line at
        # 1x whatever the price happens to be -- silently.
        with self.assertRaises(UnknownUnit):
            normalize_unit("cucharada")


class TestConvert(unittest.TestCase):
    def test_within_masa(self):
        self.assertEqual(convert(Decimal("0.5"), "kg", "g"), Decimal(500))
        self.assertEqual(convert(Decimal(500), "g", "kg"), Decimal("0.5"))
        self.assertEqual(convert(Decimal(1500), "mg", "g"), Decimal("1.5"))

    def test_within_volumen(self):
        self.assertEqual(convert(Decimal("1.5"), "l", "ml"), Decimal(1500))

    def test_identity_is_exact(self):
        self.assertEqual(convert(Decimal("180"), "g", "g"), Decimal("180"))

    def test_cross_dimension_refused(self):
        # Non-negotiable #6: no implicit 1:1 between g and ml.
        with self.assertRaises(IncompatibleUnits) as ctx:
            convert(Decimal(100), "ml", "g")
        self.assertEqual((ctx.exception.desde, ctx.exception.hacia), ("ml", "g"))

    def test_conteo_converts_to_nothing_else(self):
        with self.assertRaises(IncompatibleUnits):
            convert(Decimal(3), "unidad", "g")

    def test_roundtrip_is_lossless(self):
        original = Decimal("237.5")
        self.assertEqual(convert(convert(original, "g", "kg"), "kg", "g"), original)


class TestHelpers(unittest.TestCase):
    def test_dimension_of(self):
        self.assertEqual(dimension_of("kg"), "masa")
        self.assertEqual(dimension_of("ml"), "volumen")
        self.assertEqual(dimension_of("unidad"), "conteo")

    def test_compatible_units_never_crosses_dimensions(self):
        # Imperial mass and the gallon are here because the real catalog
        # uses them ("ALCAPARRAS 1/16OZ", 52 products with UnidadMedida
        # "Gal"). The assertion is exact rather than a subset check: this is
        # the list the recipe builder's unit selector offers, and a unit
        # silently appearing or disappearing from it is worth failing on.
        self.assertEqual(set(compatible_units("g")), {"mg", "g", "kg", "oz", "lb"})
        self.assertEqual(set(compatible_units("l")), {"ml", "l", "gal"})
        self.assertEqual(compatible_units("unidad"), ["unidad"])

    def test_imperial_and_gallon_convert_correctly(self):
        self.assertEqual(convert(Decimal(1), "lb", "g"), Decimal("453.59237"))
        self.assertEqual(convert(Decimal(16), "oz", "g"), Decimal("453.592370000"))
        self.assertEqual(convert(Decimal(1), "gal", "l"), Decimal("3.785411784"))

    def test_to_canonical(self):
        self.assertEqual(to_canonical(Decimal(2), "kg"), (Decimal(2000), "g"))
        self.assertEqual(to_canonical(Decimal(1), "l"), (Decimal(1000), "ml"))


if __name__ == "__main__":
    unittest.main()
