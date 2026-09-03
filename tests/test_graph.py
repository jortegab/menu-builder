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
    depth_below,
    depth_through_edge,
    find_cycle,
    height_above,
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
    return Insumo(id=iid, nombre=nombre, unidad_base=unidad, costo=costo)


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


class TestRecipeChoosesTheUnit(unittest.TestCase):
    """The unit belongs to the recipe line, not to the insumo.

    An insumo is *costed* in one base unit (g, ml or unidad) because a cost
    has to be per something. What a recipe measures in is a separate
    question, answered per line: the same aceite costed per ml can be called
    for in litres, millilitres or fluid ounces, and each converts.
    """

    def plato(self, cantidad, unidad, insumo_unidad, precio):
        insumos = {1: insumo(1, "X", insumo_unidad, precio)}
        recetas = {
            1: Receta(1, "Plato", "plato", porciones=1,
                      lineas=[Linea("insumo", 1, Decimal(str(cantidad)), unidad)])
        }
        return cost_recipe(1, recetas, insumos).costo_total

    def test_a_ml_insumo_can_be_used_in_l_ml_and_oz(self):
        # CRC 2 per ml. "oz" is a fluid ounce: 6 oz = 177.44 ml.
        self.assertEqual(self.plato(1, "l", "ml", "2"), Decimal("2000.00"))
        self.assertEqual(self.plato(500, "ml", "ml", "2"), Decimal("1000.00"))
        self.assertEqual(self.plato(6, "oz", "ml", "2"), Decimal("354.88"))

    def test_a_g_insumo_can_be_used_in_kg_g_and_lb(self):
        # CRC 4 per g. No ounces here: solids are measured in grams, and
        # "oz" means a fluid ounce in this app.
        self.assertEqual(self.plato("0.18", "kg", "g", "4"), Decimal("720.00"))
        self.assertEqual(self.plato(180, "g", "g", "4"), Decimal("720.00"))
        self.assertEqual(self.plato(1, "lb", "g", "4"), Decimal("1814.37"))

    def test_the_same_insumo_in_two_units_in_one_recipe(self):
        # Nothing stops a recipe using kg on one line and g on another;
        # both resolve against the same per-gram cost.
        insumos = {1: insumo(1, "Harina", "g", "2")}
        recetas = {
            1: Receta(1, "Pan", "plato", porciones=1, lineas=[
                Linea("insumo", 1, Decimal(1), "kg", orden=0),
                Linea("insumo", 1, Decimal(250), "g", orden=1),
            ])
        }
        self.assertEqual(cost_recipe(1, recetas, insumos).costo_total, Decimal("2500.00"))

    def test_a_volume_unit_on_a_mass_insumo_is_still_refused(self):
        # Free choice within the dimension; never across it. "6 oz de
        # harina" needs a density, which non-negotiable #6 refuses to invent.
        with self.assertRaises(IncompatibleUnits):
            self.plato(6, "oz", "g", "4")


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


class ProfundidadDelGrafo(unittest.TestCase):
    """`depth_below` / `height_above` / `depth_through_edge` -- the write-time
    depth guard. `cost_recipe` raises RecipeTooDeep on read; these are what
    let the *save* refuse the line instead, while the operator is still
    looking at it."""

    CADENA = {1: {2}, 2: {3}, 3: {4}, 4: set()}

    def test_una_hoja_no_tiene_profundidad(self):
        self.assertEqual(depth_below(self.CADENA, 4), 0)

    def test_profundidad_es_aristas_no_nodos(self):
        self.assertEqual(depth_below(self.CADENA, 1), 3)

    def test_una_raiz_no_tiene_altura(self):
        self.assertEqual(height_above(self.CADENA, 1), 0)

    def test_altura_es_el_camino_mas_largo_que_llega(self):
        self.assertEqual(height_above(self.CADENA, 4), 3)

    def test_toma_la_rama_mas_larga_no_la_primera(self):
        ady = {1: {2, 3}, 2: set(), 3: {4}, 4: {5}, 5: set()}
        self.assertEqual(depth_below(ady, 1), 3)

    def test_un_diamante_no_cuenta_dos_veces(self):
        ady = {1: {2, 3}, 2: {4}, 3: {4}, 4: set()}
        self.assertEqual(depth_below(ady, 1), 2)
        self.assertEqual(height_above(ady, 4), 2)

    def test_el_borde_nuevo_suma_los_dos_lados(self):
        arriba = {1: {2}, 2: {3}, 3: set()}
        abajo = {10: {11}, 11: {12}, 12: set()}
        ady = {**arriba, **abajo}
        # 3 sits 2 edges down; 10 has 2 edges below it; the new edge is one
        # more -- neither half looks deep on its own.
        self.assertEqual(depth_through_edge(ady, 3, 10), 5)

    def test_no_se_cuelga_con_un_ciclo(self):
        """It runs as a guard, and a guard that hangs on the input it
        guards against is not a guard."""
        ciclo = {1: {2}, 2: {3}, 3: {1}}
        self.assertIsInstance(depth_below(ciclo, 1), int)
        self.assertIsInstance(height_above(ciclo, 1), int)


if __name__ == "__main__":
    unittest.main()
