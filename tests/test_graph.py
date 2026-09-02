import datetime as dt
import unittest
from decimal import Decimal

from app.services.costing import CostResult, Observation, costo_unitario
from app.services.graph import (
    MAX_DEPTH,
    Insumo,
    Linea,
    Receta,
    RecipeCycle,
    RecipeTooDeep,
    build_adjacency,
    cost_recipe,
    find_cycle,
    would_create_cycle,
)
from app.services.units import IncompatibleUnits

HOY = dt.date(2026, 9, 2)


def insumo(iid: int, nombre: str, unidad: str, precio: str | None, merma: str = "0") -> Insumo:
    if precio is None:
        costo = CostResult(valor=None, metodo="last", ventana_dias=90,
                           observaciones_usadas=0, desde=None)
    else:
        serie = [Observation(HOY, Decimal(precio), Decimal(1))]
        costo = costo_unitario(serie, Decimal(merma), "last", 90, HOY)
    return Insumo(id=iid, nombre=nombre, unidad_uso=unidad, costo=costo)


class TestCycleDetection(unittest.TestCase):
    def test_acyclic_graph_has_no_cycle(self):
        self.assertIsNone(find_cycle({1: {2, 3}, 2: {3}, 3: set()}))

    def test_direct_cycle_found(self):
        self.assertIsNotNone(find_cycle({1: {2}, 2: {1}}))

    def test_transitive_cycle_found(self):
        ciclo = find_cycle({1: {2}, 2: {3}, 3: {1}})
        self.assertIsNotNone(ciclo)
        self.assertEqual(ciclo[0], ciclo[-1])

    def test_self_reference_is_a_cycle(self):
        self.assertIsNotNone(find_cycle({1: {1}}))

    def test_diamond_is_not_a_cycle(self):
        # A salsa used by two platos that both feed a combo is a DAG, not a
        # loop -- the most common shape a naive visited-set check gets wrong.
        self.assertIsNone(find_cycle({1: {2, 3}, 2: {4}, 3: {4}, 4: set()}))

    def test_would_create_cycle_rejects_the_closing_edge(self):
        adj = {1: {2}, 2: set()}
        self.assertIsNotNone(would_create_cycle(adj, 2, 1))

    def test_would_create_cycle_allows_a_safe_edge(self):
        adj = {1: {2}, 2: set(), 3: set()}
        self.assertIsNone(would_create_cycle(adj, 1, 3))

    def test_would_create_cycle_does_not_mutate_the_input(self):
        adj = {1: {2}, 2: set()}
        would_create_cycle(adj, 2, 1)
        self.assertEqual(adj, {1: {2}, 2: set()})

    def test_self_edge_rejected(self):
        self.assertEqual(would_create_cycle({1: set()}, 1, 1), [1, 1])

    def test_build_adjacency_ignores_insumo_lines(self):
        recetas = {
            1: Receta(1, "Plato", "plato", porciones=1, lineas=[
                Linea("insumo", 10, Decimal(1), "g"),
                Linea("receta", 2, Decimal(1), "g"),
            ]),
            2: Receta(2, "Salsa", "preparacion", rendimiento_cantidad=Decimal(100),
                      rendimiento_unidad="g"),
        }
        self.assertEqual(build_adjacency(recetas), {1: {2}, 2: set()})


class TestRecipeCost(unittest.TestCase):
    def test_simple_plato(self):
        insumos = {
            1: insumo(1, "Pechuga", "g", "4.00"),
            2: insumo(2, "Sal", "g", "0.50"),
        }
        recetas = {
            1: Receta(1, "Pollo", "plato", porciones=2, lineas=[
                Linea("insumo", 1, Decimal(180), "g", orden=0),
                Linea("insumo", 2, Decimal(2), "g", orden=1),
            ])
        }
        r = cost_recipe(1, recetas, insumos)
        self.assertEqual(r.estado, "completo")
        self.assertEqual(r.costo_total, Decimal("721.00"))
        self.assertEqual(r.costo_por_porcion, Decimal("360.50"))

    def test_line_unit_converted_to_the_insumo_unit(self):
        insumos = {1: insumo(1, "Pechuga", "g", "4.00")}
        recetas = {
            1: Receta(1, "Pollo", "plato", porciones=1, lineas=[
                Linea("insumo", 1, Decimal("0.18"), "kg")
            ])
        }
        self.assertEqual(cost_recipe(1, recetas, insumos).costo_total, Decimal("720.00"))

    def test_merma_raises_the_cost(self):
        insumos = {1: insumo(1, "Pechuga", "g", "4.00", merma="20")}
        recetas = {
            1: Receta(1, "Pollo", "plato", porciones=1, lineas=[
                Linea("insumo", 1, Decimal(100), "g")
            ])
        }
        # 4.00 / 0.8 = 5.00 per usable gram.
        self.assertEqual(cost_recipe(1, recetas, insumos).costo_total, Decimal("500.00"))

    def test_preparacion_priced_by_its_yield(self):
        insumos = {1: insumo(1, "Tomate", "g", "2.00")}
        recetas = {
            2: Receta(2, "Salsa criolla", "preparacion",
                      rendimiento_cantidad=Decimal(500), rendimiento_unidad="g",
                      lineas=[Linea("insumo", 1, Decimal(1000), "g")]),
            1: Receta(1, "Pollo", "plato", porciones=1,
                      lineas=[Linea("receta", 2, Decimal(50), "g")]),
        }
        # 1000 g x ₡2 = ₡2 000 for 500 g of salsa = ₡4/g; 50 g = ₡200.
        # The 500 g lost to reduction is priced into what survived.
        self.assertEqual(cost_recipe(2, recetas, insumos).costo_total, Decimal("2000.00"))
        self.assertEqual(cost_recipe(1, recetas, insumos).costo_total, Decimal("200.00"))

    def test_nested_preparaciones(self):
        insumos = {1: insumo(1, "Hueso", "g", "1.00")}
        recetas = {
            3: Receta(3, "Fondo", "preparacion", rendimiento_cantidad=Decimal(100),
                      rendimiento_unidad="ml", lineas=[Linea("insumo", 1, Decimal(200), "g")]),
            2: Receta(2, "Salsa", "preparacion", rendimiento_cantidad=Decimal(100),
                      rendimiento_unidad="g", lineas=[Linea("receta", 3, Decimal(50), "ml")]),
            1: Receta(1, "Plato", "plato", porciones=1,
                      lineas=[Linea("receta", 2, Decimal(100), "g")]),
        }
        # Fondo: 200 g x ₡1 = ₡200 / 100 ml = ₡2/ml.
        # Salsa: 50 ml x ₡2 = ₡100 / 100 g = ₡1/g. Plato: 100 g = ₡100.
        self.assertEqual(cost_recipe(1, recetas, insumos).costo_total, Decimal("100.00"))

    def test_missing_price_makes_the_whole_recipe_incompleto(self):
        # D10: no partial sum is returned, ever.
        insumos = {
            1: insumo(1, "Pechuga", "g", "4.00"),
            2: insumo(2, "Trufa", "g", None),
        }
        recetas = {
            1: Receta(1, "Pollo", "plato", porciones=1, lineas=[
                Linea("insumo", 1, Decimal(180), "g", orden=0),
                Linea("insumo", 2, Decimal(2), "g", orden=1),
            ])
        }
        r = cost_recipe(1, recetas, insumos)
        self.assertEqual(r.estado, "incompleto")
        self.assertIsNone(r.costo_total)
        self.assertIsNone(r.costo_por_porcion)
        self.assertEqual(r.faltantes, ["Trufa"])
        # The priced line still reports its own cost, so the builder can
        # show what *is* known while refusing to total it.
        self.assertEqual(r.lineas[0].costo, Decimal("720.00"))
        self.assertTrue(r.lineas[1].faltante)

    def test_incompleteness_propagates_up_from_a_preparacion(self):
        insumos = {1: insumo(1, "Trufa", "g", None)}
        recetas = {
            2: Receta(2, "Salsa", "preparacion", rendimiento_cantidad=Decimal(100),
                      rendimiento_unidad="g", lineas=[Linea("insumo", 1, Decimal(10), "g")]),
            1: Receta(1, "Plato", "plato", porciones=1,
                      lineas=[Linea("receta", 2, Decimal(50), "g")]),
        }
        r = cost_recipe(1, recetas, insumos)
        self.assertEqual(r.estado, "incompleto")
        self.assertIsNone(r.costo_total)
        self.assertEqual(r.faltantes, ["Trufa"])

    def test_shared_preparacion_is_memoized_and_consistent(self):
        insumos = {1: insumo(1, "Tomate", "g", "2.00")}
        recetas = {
            2: Receta(2, "Salsa", "preparacion", rendimiento_cantidad=Decimal(100),
                      rendimiento_unidad="g", lineas=[Linea("insumo", 1, Decimal(100), "g")]),
            1: Receta(1, "Plato", "plato", porciones=1, lineas=[
                Linea("receta", 2, Decimal(50), "g", orden=0),
                Linea("receta", 2, Decimal(50), "g", orden=1),
            ]),
        }
        r = cost_recipe(1, recetas, insumos)
        self.assertEqual(r.costo_total, Decimal("200.00"))
        self.assertEqual(r.lineas[0].costo, r.lineas[1].costo)

    def test_incompatible_line_unit_raises(self):
        # A data error, not a costing outcome: the save-time validation
        # should have refused this line, so it must not read as "no price".
        insumos = {1: insumo(1, "Aceite", "ml", "3.00")}
        recetas = {
            1: Receta(1, "Plato", "plato", porciones=1,
                      lineas=[Linea("insumo", 1, Decimal(10), "g")])
        }
        with self.assertRaises(IncompatibleUnits):
            cost_recipe(1, recetas, insumos)

    def test_cycle_surviving_to_read_time_raises_instead_of_hanging(self):
        insumos = {}
        recetas = {
            1: Receta(1, "A", "preparacion", rendimiento_cantidad=Decimal(1),
                      rendimiento_unidad="g", lineas=[Linea("receta", 2, Decimal(1), "g")]),
            2: Receta(2, "B", "preparacion", rendimiento_cantidad=Decimal(1),
                      rendimiento_unidad="g", lineas=[Linea("receta", 1, Decimal(1), "g")]),
        }
        with self.assertRaises(RecipeCycle):
            cost_recipe(1, recetas, insumos)

    def test_too_deep_raises(self):
        insumos = {1: insumo(1, "Base", "g", "1.00")}
        recetas = {}
        profundidad = MAX_DEPTH + 3
        for i in range(1, profundidad + 1):
            hijo = i + 1
            lineas = (
                [Linea("insumo", 1, Decimal(1), "g")]
                if i == profundidad
                else [Linea("receta", hijo, Decimal(1), "g")]
            )
            recetas[i] = Receta(i, f"N{i}", "preparacion", rendimiento_cantidad=Decimal(1),
                                rendimiento_unidad="g", lineas=lineas)
        with self.assertRaises(RecipeTooDeep):
            cost_recipe(1, recetas, insumos)

    def test_lines_are_costed_in_orden(self):
        insumos = {1: insumo(1, "A", "g", "1.00"), 2: insumo(2, "B", "g", "1.00")}
        recetas = {
            1: Receta(1, "Plato", "plato", porciones=1, lineas=[
                Linea("insumo", 2, Decimal(1), "g", orden=5),
                Linea("insumo", 1, Decimal(1), "g", orden=1),
            ])
        }
        self.assertEqual([ln.nombre for ln in cost_recipe(1, recetas, insumos).lineas], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
