"""`services/insumos.py` -- insumo master data and its validation rules.

The rules are checked here rather than left to the database's CHECK
constraints for a reason worth pinning: a constraint violation arrives as
an opaque IntegrityError at commit time, which the operator experiences as
a 500 with their half-filled form gone. These tests are mostly about the
error being *useful*, not merely present.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import CatalogProduct, Insumo, Receta, RecetaLinea  # noqa: E402
from app.services import insumos as svc  # noqa: E402


class TestSlug(unittest.TestCase):
    def test_folds_accents_rather_than_dropping_them(self):
        # Otherwise one typo makes two different slugs for one ingredient.
        self.assertEqual(svc.slugify("Pechuga deshuesáda"), "pechuga-deshuesada")
        self.assertEqual(svc.slugify("Pechuga deshuesada"), "pechuga-deshuesada")

    def test_handles_punctuation_and_case(self):
        self.assertEqual(svc.slugify("  Aceite de Oliva (Extra Virgen) "), "aceite-de-oliva-extra-virgen")

    def test_never_returns_empty(self):
        self.assertEqual(svc.slugify("!!!"), "insumo")


class TestValidation(unittest.TestCase):
    def base(self, **kw):
        form = {"nombre": "Pechuga", "unidad_base": "g"}
        form.update(kw)
        return form

    def test_accepts_a_minimal_insumo(self):
        fields = svc.validate(**self.base())
        self.assertEqual(fields["nombre"], "Pechuga")
        self.assertEqual(fields["merma_pct"], Decimal(0))
        # Empty method/window mean "follow the app default", stored as NULL.
        self.assertIsNone(fields["metodo_costeo"])
        self.assertIsNone(fields["ventana_dias"])

    def test_name_is_required(self):
        with self.assertRaises(svc.ValidationError) as ctx:
            svc.validate(**self.base(nombre="   "))
        self.assertIn("nombre", ctx.exception.errors)

    def test_unit_must_be_one_we_can_cost_in(self):
        # "kg" is arithmetically fine and reads badly on every recipe
        # (0.18 kg of pechuga), so it is not offered as a unidad de uso.
        with self.assertRaises(svc.ValidationError) as ctx:
            svc.validate(**self.base(unidad_base="kg"))
        self.assertIn("unidad_base", ctx.exception.errors)

    def test_comma_is_a_valid_decimal_separator(self):
        # Costa Rica writes 12,5. A form that rejects it fights its user.
        self.assertEqual(svc.validate(**self.base(merma_pct="12,5"))["merma_pct"], Decimal("12.5"))
        self.assertEqual(svc.validate(**self.base(merma_pct="12.5"))["merma_pct"], Decimal("12.5"))

    def test_merma_at_or_above_the_cap_is_rejected(self):
        for bad in ("95", "99", "995"):
            with self.assertRaises(svc.ValidationError, msg=bad) as ctx:
                svc.validate(**self.base(merma_pct=bad))
            self.assertIn("merma", ctx.exception.errors["merma_pct"].lower())

    def test_merma_just_under_the_cap_is_allowed(self):
        self.assertEqual(svc.validate(**self.base(merma_pct="94.9"))["merma_pct"], Decimal("94.9"))

    def test_negative_merma_is_rejected(self):
        with self.assertRaises(svc.ValidationError) as ctx:
            svc.validate(**self.base(merma_pct="-1"))
        self.assertIn("merma_pct", ctx.exception.errors)

    def test_unparseable_merma_says_what_a_good_value_looks_like(self):
        with self.assertRaises(svc.ValidationError) as ctx:
            svc.validate(**self.base(merma_pct="mucho"))
        self.assertIn("12", ctx.exception.errors["merma_pct"])

    def test_method_must_be_one_of_the_offered_ones(self):
        with self.assertRaises(svc.ValidationError):
            svc.validate(**self.base(metodo_costeo="mediana"))
        self.assertEqual(
            svc.validate(**self.base(metodo_costeo="median_window"))["metodo_costeo"],
            "median_window",
        )

    def test_window_bounds(self):
        self.assertEqual(svc.validate(**self.base(ventana_dias="30"))["ventana_dias"], 30)
        for bad in ("0", "-5", str(svc.MAX_VENTANA_DIAS + 1), "muchos"):
            with self.assertRaises(svc.ValidationError, msg=bad):
                svc.validate(**self.base(ventana_dias=bad))

    def test_errors_are_keyed_by_field(self):
        # So the template can put each message beside its own input.
        with self.assertRaises(svc.ValidationError) as ctx:
            svc.validate(nombre="", unidad_base="litros", merma_pct="200")
        self.assertEqual(
            sorted(ctx.exception.errors), ["merma_pct", "nombre", "unidad_base"]
        )

    def test_blank_optional_text_becomes_null_not_empty_string(self):
        fields = svc.validate(**self.base(categoria_slug="  ", notas=""))
        self.assertIsNone(fields["categoria_slug"])
        self.assertIsNone(fields["notas"])


class TestCrud(DbTestCase):
    def test_create_assigns_a_slug(self):
        insumo = svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        self.assertEqual(insumo.slug, "pechuga-deshuesada")
        self.assertTrue(insumo.activo)

    def test_two_insumos_may_share_a_name(self):
        # The name is not the identity; the row is. Failing the save here
        # would be wrong.
        a = svc.create(self.db, nombre="Salsa", unidad_base="ml")
        b = svc.create(self.db, nombre="Salsa", unidad_base="ml")
        self.assertEqual(a.slug, "salsa")
        self.assertEqual(b.slug, "salsa-2")

    def test_update_reslugs_only_when_the_name_changed(self):
        insumo = svc.create(self.db, nombre="Pechuga", unidad_base="g")
        svc.update(self.db, insumo, nombre="Pechuga", unidad_base="g", merma_pct="12")
        self.assertEqual(insumo.slug, "pechuga")
        self.assertEqual(insumo.merma_pct, Decimal(12))

        svc.update(self.db, insumo, nombre="Pechuga deshuesada", unidad_base="g")
        self.assertEqual(insumo.slug, "pechuga-deshuesada")

    def test_update_can_clear_back_to_the_app_default(self):
        insumo = svc.create(
            self.db, nombre="Pechuga", unidad_base="g",
            metodo_costeo="max_window", ventana_dias="30",
        )
        self.assertEqual(insumo.metodo_costeo, "max_window")
        svc.update(self.db, insumo, nombre="Pechuga", unidad_base="g")
        self.assertIsNone(insumo.metodo_costeo)
        self.assertIsNone(insumo.ventana_dias)

    def test_invalid_update_does_not_write(self):
        insumo = svc.create(self.db, nombre="Pechuga", unidad_base="g", merma_pct="10")
        with self.assertRaises(svc.ValidationError):
            svc.update(self.db, insumo, nombre="Pechuga", unidad_base="g", merma_pct="200")
        self.db.expire_all()
        self.assertEqual(self.db.get(Insumo, insumo.id).merma_pct, Decimal(10))

    def test_archive_never_deletes(self):
        # An archived insumo may still be referenced by a receta_linea;
        # deleting it would orphan a recipe or silently change a cost.
        insumo = svc.create(self.db, nombre="Pechuga", unidad_base="g")
        svc.set_activo(self.db, insumo, False)
        self.assertFalse(insumo.activo)
        self.assertIsNotNone(self.db.get(Insumo, insumo.id))
        svc.set_activo(self.db, insumo, True)
        self.assertTrue(insumo.activo)


class TestListing(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = svc.create(self.db, nombre="Pechuga deshuesada", unidad_base="g")
        self.b = svc.create(self.db, nombre="Aceite de oliva", unidad_base="ml")
        self.c = svc.create(self.db, nombre="Perejil", unidad_base="g")
        svc.set_activo(self.db, self.c, False)

    def test_defaults_to_active_only(self):
        self.assertEqual([i.nombre for i in svc.listar(self.db)], ["Aceite de oliva", "Pechuga deshuesada"])

    def test_archived_and_all(self):
        self.assertEqual([i.nombre for i in svc.listar(self.db, activo=False)], ["Perejil"])
        self.assertEqual(len(svc.listar(self.db, activo=None)), 3)

    def test_search_is_case_insensitive_and_partial(self):
        self.assertEqual([i.nombre for i in svc.listar(self.db, q="PECH")], ["Pechuga deshuesada"])

    def test_sorted_by_name(self):
        nombres = [i.nombre for i in svc.listar(self.db, activo=None)]
        self.assertEqual(nombres, sorted(nombres))


class TestCounts(DbTestCase):
    def test_usage_count_reflects_recipe_lines(self):
        insumo = svc.create(self.db, nombre="Pechuga", unidad_base="g")
        self.assertEqual(svc.usage_count(self.db, insumo), 0)

        receta = Receta(nombre="Pollo", slug="pollo", tipo="plato", porciones=1)
        self.db.add(receta)
        self.db.commit()
        self.db.add(
            RecetaLinea(
                receta_id=receta.id, tipo_componente="insumo", insumo_id=insumo.id,
                cantidad=Decimal(180), unidad="g",
            )
        )
        self.db.commit()
        self.assertEqual(svc.usage_count(self.db, insumo), 1)

    def test_fuente_counts_is_empty_for_no_ids(self):
        self.assertEqual(svc.fuente_counts(self.db, []), {})

    def test_categorias_come_from_the_mirror(self):
        # Never a hardcoded copy of upstream's taxonomy -- it has changed
        # twice already (alimentos_* -> insumos_* -> alimentos_*).
        self.db.add_all([
            CatalogProduct(internal_code="1-a", upstream_product_id=1, supplier_id=1,
                           supplier_nombre="X", description="A", categoria_slug="alimentos_carnes"),
            CatalogProduct(internal_code="1-b", upstream_product_id=2, supplier_id=1,
                           supplier_nombre="X", description="B", categoria_slug="alimentos_verduras"),
            CatalogProduct(internal_code="1-c", upstream_product_id=3, supplier_id=1,
                           supplier_nombre="X", description="C", categoria_slug="alimentos_carnes"),
        ])
        self.db.commit()
        self.assertEqual(
            svc.categorias_disponibles(self.db), ["alimentos_carnes", "alimentos_verduras"]
        )


if __name__ == "__main__":
    unittest.main()
