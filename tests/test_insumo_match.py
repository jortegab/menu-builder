"""`services/insumo_match.py` -- reading a pack size out of an invoice
description, and scoring a description against an insumo name.

Every fixture here is a **real description from the live catalog**. That is
deliberate: a parser tested only against invented strings passes and then
meets `CHEESECAKE ORIG 10" 12 PORC 1/1.5 KGS`.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import insumo_match as m  # noqa: E402


class TestParsePresentacion(unittest.TestCase):
    def test_slash_form_with_one_package(self):
        # 352 of the ~420 slash forms in the live catalog are `1/...`, where
        # multiplying and discarding the count agree -- which is exactly why
        # the rule went untested for so long.
        self.assertEqual(m.parse_presentacion("TOCINETA AHUMADA MFS 1/1KG"), (Decimal(1), "kg"))
        self.assertEqual(m.parse_presentacion("QUESO PARMESANO 1/500 GRS"), (Decimal(500), "g"))
        self.assertEqual(m.parse_presentacion("ACEITE OLEINA MFS 1/17 L"), (Decimal(17), "l"))

    def test_slash_form_multiplies_to_the_total(self):
        # The ~65 rows where it mattered, and where an earlier version was
        # wrong by the case size.
        self.assertEqual(m.parse_presentacion("HARINA NACARINA 4/2.5 KG"), (Decimal("10.0"), "kg"))
        self.assertEqual(m.parse_presentacion("AGUA SAN ANGEL TP 24/600 ML"), (Decimal(14400), "ml"))
        self.assertEqual(
            m.parse_presentacion('TORTILLA TRIGO MISSION CONG 10.5" 6/12 UDS.'),
            (Decimal(72), "unidad"),
        )

    def test_the_description_that_states_its_own_total(self):
        # `MARGARINA BLOQUES CORONA 6/2.08KG (12,5KG)` is the proof that the
        # slash form multiplies: 6 x 2.08 = 12.48, and the parenthetical
        # says 12,5. Discarding the count would cost it at 2.08 kg.
        cantidad, unidad = m.parse_presentacion("MARGARINA BLOQUES CORONA 6/2.08KG (12,5KG)")
        self.assertEqual(unidad, "kg")
        self.assertAlmostEqual(float(cantidad), 12.5, delta=0.1)

    def test_x_form_multiplies(self):
        self.assertEqual(m.parse_presentacion("CJ 24x350ML"), (Decimal(8400), "ml"))
        self.assertEqual(m.parse_presentacion("GASEOSA 12 X 500 ML"), (Decimal(6000), "ml"))

    def test_ratios_and_inch_fractions_are_not_pack_sizes(self):
        # Both real. `81/19` is a lean/fat ratio and `4 5/8"` an inch
        # fraction; neither has a unit after the second number, so neither
        # matches -- and the bare-quantity rule then correctly finds the
        # 4.5KG further along the first one.
        self.assertEqual(
            m.parse_presentacion("CARNE MOLIDA RES 81/19 TUBO 4.5KG APROX(PXKG)"),
            (Decimal("4.5"), "kg"),
        )
        self.assertIsNone(m.parse_presentacion('PAN HAMBURGUESA BRIOCHE 4 5/8"'))

    def test_bare_quantity(self):
        self.assertEqual(m.parse_presentacion("BROCOLI (600 GR C/U)W"), (Decimal(600), "g"))
        self.assertEqual(m.parse_presentacion("BOLSA 2.5 KG"), (Decimal("2.5"), "kg"))

    def test_decimals_in_either_notation(self):
        self.assertEqual(m.parse_presentacion("SALSA TERIYAKI 1/3.78LT"), (Decimal("3.78"), "l"))
        self.assertEqual(m.parse_presentacion("CHEESECAKE 1/1,5 KGS"), (Decimal("1.5"), "kg"))

    def test_imperial_units_on_imported_goods(self):
        self.assertEqual(m.parse_presentacion("ALCAPARRAS IMPORTADAS 1/16OZ"), (Decimal(16), "oz"))

    def test_gallons(self):
        self.assertEqual(m.parse_presentacion("CLORO ECONOMICO MFS 1/1 GL"), (Decimal(1), "gal"))

    def test_counts(self):
        self.assertEqual(m.parse_presentacion("TORTIRICAS FREIR GDE / 30 UND"), (Decimal(30), "unidad"))

    def test_a_number_without_a_unit_is_not_a_pack_size(self):
        # "10X12" is a bag's dimensions. Requiring a unit is what keeps this
        # from inventing a factor of 120 for vacuum bags.
        self.assertIsNone(m.parse_presentacion("BOLSAS AL VACIO 10X12"))
        self.assertIsNone(m.parse_presentacion('CHEESECAKE ORIG 10" 12 PORC'))

    def test_descriptions_with_nothing_to_read(self):
        for desc in ("TORTILLA PEQUENA BLANCA", "PELLEJO CERDO", "JALAPEÑO VERDE", ""):
            self.assertIsNone(m.parse_presentacion(desc), desc)

    def test_every_volume_unit_still_parses(self):
        # Pinned because a careless edit once dropped ml/l/lt from the
        # alternation, which silently halved the parser's coverage.
        for text, expected in [
            ("X 1/750 ML", (Decimal(750), "ml")),
            ("X 1/2 LT", (Decimal(2), "l")),
            ("X 1/1 LITRO", (Decimal(1), "l")),
            ("X 1/1 GAL", (Decimal(1), "gal")),
        ]:
            self.assertEqual(m.parse_presentacion(text), expected, text)


class TestResolveFactor(unittest.TestCase):
    def test_description_wins(self):
        factor, origen = m.resolve_factor(
            descripcion="PECHUGA DESHUESADA 1/5KG", unidad_base="g",
            unidad_medida="Unid", categoria_slug="insumos_pollo",
        )
        self.assertEqual(factor, Decimal(5000))
        self.assertEqual(origen, "descripcion")

    def test_falls_back_to_the_xml_unit(self):
        factor, origen = m.resolve_factor(
            descripcion="PECHUGA DESHUESADA", unidad_base="g", unidad_medida="Kg",
        )
        self.assertEqual(factor, Decimal(1000))
        self.assertEqual(origen, "unidad_medida")

    def test_falls_back_to_the_category_default(self):
        # The operator's own rule: meats, chicken and seafood are in kg.
        factor, origen = m.resolve_factor(
            descripcion="POSTA CERDO", unidad_base="g",
            unidad_medida="Unid", categoria_slug="insumos_cerdo",
        )
        self.assertEqual(factor, Decimal(1000))
        self.assertEqual(origen, "default_categoria")

    def test_unresolved_is_none_never_a_guess(self):
        # A None factor keeps the fuente unusable and in the queue. A 1
        # would silently cost the insumo per package.
        factor, origen = m.resolve_factor(
            descripcion="TORTILLA PEQUENA BLANCA", unidad_base="g",
            unidad_medida="Otros", categoria_slug="insumos_abarrotes",
        )
        self.assertIsNone(factor)
        self.assertEqual(origen, "sin_resolver")

    def test_a_pack_size_in_the_wrong_dimension_does_not_force_a_conversion(self):
        # "500 ML" on something measured in grams means the unidad de uso is
        # probably wrong -- a person's call, not a conversion to invent.
        factor, origen = m.resolve_factor(descripcion="SALSA 1/500 ML", unidad_base="g")
        self.assertIsNone(factor)
        self.assertEqual(origen, "sin_resolver")

    def test_category_default_does_not_apply_to_volume(self):
        factor, origen = m.resolve_factor(
            descripcion="ACEITE", unidad_base="ml", categoria_slug="insumos_carnes"
        )
        self.assertIsNone(factor)
        self.assertEqual(origen, "sin_resolver")

    def test_ounces_convert(self):
        factor, _ = m.resolve_factor(descripcion="ALCAPARRAS 1/16OZ", unidad_base="g")
        self.assertEqual(factor, Decimal("28.349523125") * 16)


class TestTokens(unittest.TestCase):
    def test_strips_packaging_units_and_numbers(self):
        self.assertEqual(
            m.tokens_significativos("FILETE DE PECHUGA DESHUESADA MFS 1/5 KG"),
            ["filete", "pechuga", "deshuesada"],
        )

    def test_folds_accents(self):
        self.assertEqual(m.tokens_significativos("JALAPEÑO VERDE"), ["jalapeno", "verde"])

    def test_drops_a_number_glued_to_its_unit(self):
        # "1kg" is neither a bare digit nor a recognised unit on its own, so
        # it used to survive into proposed names ("Paprika 454gr").
        self.assertEqual(
            m.tokens_significativos("BATTER CUSTOM CULINARY 1/1KG"),
            ["batter", "custom", "culinary"],
        )
        self.assertEqual(m.tokens_significativos("PAPRIKA MFS 1/454GR"), ["paprika"])

    def test_keeps_words_that_merely_contain_digits_elsewhere(self):
        # Only a *leading* digit means quantity. "41-50" is a shrimp size
        # grade and normalizes to two numbers, both dropped; the name stays.
        self.assertEqual(m.tokens_significativos("CAMARON 41-50"), ["camaron"])


class TestSimilitud(unittest.TestCase):
    def test_the_motivating_case_scores_high(self):
        # The example from PLAN.md §0: two suppliers, one ingredient.
        a = m.similitud("FILETE DE PECHUGA DESHUESADA MFS 1/5KG", "Pechuga deshuesada")
        b = m.similitud("PECHUGA DESHUESADA", "Pechuga deshuesada")
        self.assertGreater(a, 0.5)
        self.assertEqual(b, 1.0)

    def test_different_ingredients_score_low(self):
        # "PECHUGA" and "PESCADO" are 57% similar as raw strings and share
        # nothing that matters -- which is why tokens outweigh sequence.
        self.assertLess(m.similitud("PESCADO ENTERO", "Pechuga deshuesada"), 0.35)

    def test_empty_scores_zero(self):
        self.assertEqual(m.similitud("", "Pechuga"), 0.0)
        self.assertEqual(m.similitud("1/5 KG", "Pechuga"), 0.0)


class TestConflictos(unittest.TestCase):
    """The over-merge guard. Every case here is from the live catalog."""

    def test_con_hueso_is_never_deshuesada(self):
        # Scored 0.47 and was offered as a match before this guard existed.
        # Those two differ by ~30 points of merma; merging them is not a
        # small error.
        self.assertEqual(m.similitud("PECHUGA CON HUESO", "Pechuga deshuesada"), 0.0)
        self.assertIsNone(
            m.mejor_match("PECHUGA CON HUESO", [(1, "Pechuga deshuesada")])
        )

    def test_the_guard_does_not_break_correct_matches(self):
        for desc in (
            "Filete de Pechuga Deshuesada",       # AVICOLA ALTAVISTA
            "PECHUGA DE POLLO DESHUESADA",        # Diego Armando Alvarado
            "Recorte de pechuga deshuesada",      # Hevar
        ):
            self.assertGreater(m.similitud(desc, "Pechuga deshuesada"), 0.5, desc)

    def test_a_typo_still_matches(self):
        # `PECHUGA DE POLLO DESGUESADA` is real. It shares no token with
        # "deshuesada", so a rule that penalised missing qualifiers would
        # throw it away -- which is why the guard needs both sides to
        # actively disagree, not merely differ.
        self.assertGreater(
            m.similitud("PECHUGA DE POLLO DESGUESADA", "Pechuga deshuesada"), 0.35
        )

    def test_other_conflict_pairs(self):
        self.assertEqual(m.similitud("CARNE MOLIDA RES", "Carne entera"), 0.0)
        self.assertEqual(m.similitud("JAMON AHUMADO", "Jamon crudo"), 0.0)

    def test_conflict_is_symmetric(self):
        self.assertEqual(m.similitud("Pechuga deshuesada", "PECHUGA CON HUESO"), 0.0)


class TestMejorMatch(unittest.TestCase):
    CANDIDATOS = [(1, "Pechuga deshuesada"), (2, "Aceite de oliva"), (3, "Perejil")]

    def test_picks_the_best_candidate(self):
        match = m.mejor_match("FILETE DE PECHUGA DESHUESADA 1/5KG", self.CANDIDATOS)
        self.assertIsNotNone(match)
        self.assertEqual(match[0], 1)

    def test_returns_none_below_the_floor(self):
        # A bad suggestion is worse than none: it trains the operator to
        # click through without reading.
        self.assertIsNone(m.mejor_match("PAPEL HIGIENICO JUMBO", self.CANDIDATOS))

    def test_no_candidates(self):
        self.assertIsNone(m.mejor_match("PECHUGA", []))


if __name__ == "__main__":
    unittest.main()
