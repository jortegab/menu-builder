# Menu Builder

Constructor de recetas y costeo de menú para un restaurante de un solo
local en Costa Rica. Corre en la máquina del negocio, no en la nube.

Contesta una sola pregunta, y la contesta bien: **¿cuánto me cuesta este
plato, hoy?** Y la que viene después: *¿qué le pasa cuando sube el pollo?*

Los precios no se digitan. Salen de las facturas electrónicas que
**`restaurant-manager`** ya recibe del Gmail del restaurante — el mismo
historial de compras real, con descuentos, sin IVA, convertido a colones
cuando la factura vino en dólares. Este app no vuelve a calcular nada de
eso: lo lee.

> **Estado: fase 1, paso 1.2.** Ya funciona la parte de abajo: el catálogo
> de `restaurant-manager` se trae completo y se mantiene al día — **1 420
> productos y 17 645 compras**, sincronizados contra el app real. Eso se
> maneja desde **Ajustes**, que es la única pantalla que existe hoy.
>
> **Lo que todavía no existe son los insumos, las recetas y el costeo** —
> o sea, casi todo lo que se describe abajo en «qué va a hacer». El motor
> que calcula los costos sí está escrito y probado, pero no hay pantalla
> que lo use todavía. Se lista para que se entienda hacia dónde va, no
> para dar a entender que ya corre. Ver «Qué falta».

---

## Arrancar

```bash
cp .env.example .env
docker compose up --build
```

Abrí <http://localhost:8001>.

El puerto es **8001**, no 8000: `restaurant-manager` ocupa ese. Lo mismo
con la base, que queda en **5434** en vez de 5433. Los dos apps corren al
mismo tiempo en la misma máquina, cada uno con su base y su volumen.

La base vive en el volumen `menu-builder_pgdata`. El nombre del proyecto
está fijado en `docker-compose.yml` (`name: menu-builder`) a propósito:
sin esa línea Compose lo deriva del nombre de la carpeta, y renombrar el
repo dejaría la base huérfana — se lee igualito a una pérdida total de
datos. Para empezar de cero a propósito: `docker compose down -v`.

Las pruebas:

```bash
PYTHONPATH=backend python -m unittest discover -s tests -v
```

---

## Cómo se conecta con `restaurant-manager`

Los dos apps se hablan por una sola puerta: una **API de solo lectura**
(`/api/v1`) que `restaurant-manager` expone y que este app consume.

```
  facturas electrónicas (Gmail)
             |
             v
    restaurant-manager  ──── GET /api/v1/productos ────►  menu-builder
    (catálogo, precios)      GET /api/v1/.../compras      (insumos, recetas)
```

**Este app nunca le escribe a `restaurant-manager`.** Ni una categoría, ni
un mapeo, ni una corrección. Si un producto está mal categorizado, se
arregla allá. La regla vale en los dos sentidos: cada app es dueña de sus
propios datos y el otro solo lee.

Esa API **ya está construida** (`restaurant-manager` 0.75, 2 de setiembre
de 2026): seis endpoints de solo lectura, con sus pruebas, verificados
contra el catálogo real — 1 420 productos, 32 proveedores, 17 645 líneas de
compra. Ver `PLAN.md` §5.

Un detalle para tenerlo presente: el otro app corre desde una imagen, no
desde los archivos del repo, así que **la API nueva no se sirve hasta que
se reconstruya ese contenedor** (`docker compose up -d --build web` en
`restaurant-manager`).

---

## Las cuatro cosas que hay que entender

Todo el app es la relación entre estas cuatro.

### Producto

Lo que un proveedor específico te vende, con el nombre que él le puso y al
precio que él te cobra. Viene de `restaurant-manager` y acá no se edita
nunca.

Dos proveedores del mismo pollo son **dos productos**, y eso está bien:
son dos precios, de dos proveedores, y juntarlos destruiría el historial
que hace que funcionen las alertas de precio del otro app.

### Insumo

Lo que la cocina usa. *Pechuga deshuesada* es **un** insumo, aunque hoy
venga de Aviocal Altavista como «filete de pechuga deshuesada» y de
Inversiones Hevar como «pechuga deshuesada». Un insumo agrupa varios
productos.

El insumo carga las tres cosas que un producto no puede saber:

- **Unidad de uso** — en qué mide la cocina. Se compra en paquetes de 5 kg
  y se usa en gramos: la unidad de uso es el gramo, y el precio del paquete
  se divide entre 5 000.
- **% de merma** — cuánto se pierde al preparar. Comprás pechuga a ₡4 000
  el kilo con 12% de merma y el kilo *usable* te cuesta ₡4 545. El costo se
  reparte entre lo que sobrevive, no entre lo que entró.
- **Método de costeo** — cuál precio creer. Último, promedio, promedio
  ponderado por cantidad, mediana, máximo o mínimo, sobre una ventana de
  días que vos elegís (90 por defecto). El máximo sirve para costear
  conservador; el ponderado es el promedio honesto de lo que de verdad
  pagaste.

  **Por defecto se usa la mediana**, y no es una preferencia: en el
  catálogo real, 6 de cada 100 productos con historial suficiente traen
  alguna línea de factura mala. El perejil, por ejemplo, se compra entre
  ₡850 y ₡1 190 — y hay una línea de ₡120.79. La mediana no la ve; un
  promedio se la come. Y donde no hace falta no cuesta nada: en el 54% de
  los productos, que tienen dos compras o menos en la ventana, la mediana
  da exactamente lo mismo que cualquier otro método.

### Preparación (insumo compuesto o subreceta)

Algo que se hace en casa y que otras recetas usan como si fuera un insumo:
una salsa, una marinada, un fondo, una masa.

Tiene su propia receta **y un rendimiento**: metés 3 000 g de tomate y te
salen 2 500 g de salsa. Ese par es lo que permite cobrarla por gramo. Los
500 g que se evaporaron quedan pagados por los que sobrevivieron.

Una salsa que usan seis platos se edita **una vez** y los seis se
recostean.

### Receta (plato)

El ítem del menú. Igual que una preparación, pero en vez de rendimiento
declara porciones.

---

## Qué va a hacer (fase 1, todavía sin construir)

> Lo de esta sección **no está construido**, con una excepción: traer el
> catálogo, que ya funciona y vive en Ajustes.

### Insumos

La lista canónica: unidad de uso, merma, método, costo actual, cuántos
productos lo alimentan y cuándo se compró por última vez.

Cada insumo tiene su **«¿Por qué este costo?»**: el método y la ventana que
se aplicaron, la serie completa de compras con fecha, proveedor y factura,
cuántas observaciones se descartaron y por qué, el factor de conversión y
de dónde salió, y la merma. Ningún número aparece sin poder explicarse.

### Mapeo

La cola de trabajo. Los productos del catálogo que todavía no son ningún
insumo, agrupados por sugerencia, para confirmar de a montones: *«estas
cuatro filas son todas pechuga deshuesada»*, un clic.

Las sugerencias las hace un LLM — el mismo enfoque que ya usa
`restaurant-manager` para categorizar productos. Lee la descripción y
propone a qué insumo pertenece; de paso saca la presentación (`1/5kg` →
paquete de 5 kg → 5 000 g) y las unidades que faltan. Cuando la
descripción no dice nada, cae a la unidad de la factura, y si tampoco,
a la de la categoría — carnes, pollo y mariscos en kilos.

En la práctica el peso lo lleva la descripción, no la factura: **el 85% del
catálogo trae la unidad como «Unid» u «Otros»**, que no dice nada, y la
unidad comercial viene vacía en el 96,7% de las líneas. Por eso el campo
para escribir el factor a mano está a un clic: se va a usar.

El catálogo tiene **1 420 productos**, y esa lista completa no se drena de
una sentada. La cola llega filtrada: solo categorías de comida (fuera
empaques, menaje, limpieza, higiene, oficina) y solo lo comprado en los
últimos 180 días — unas 400 filas, ordenadas por volumen de compra, con el
resto detrás de un filtro. Además no hace falta mapear todo: un insumo
solo tiene que existir cuando una receta lo pide.

**Nada de eso se activa solo.** La sugerencia entra como *sugerida* y
espera. Un mapeo equivocado se vuelve invisible apenas está adentro de un
costo, así que la confirmación es de una persona, siempre. Si no hay llave
de API, o se cae la red, la cola sigue funcionando con comparación de
texto: más tosca, pero nunca se queda en blanco.

### El constructor de recetas

La pantalla para la que existe todo lo demás.

A la izquierda un buscador con los insumos y las preparaciones. **Se
arrastran** a la receta, o se agregan con un botón — arrastrar es el camino
rápido, nunca el único. Cada línea tiene su cantidad como campo numérico y
un selector de unidad que solo ofrece unidades compatibles. Se cambia un
número y **el costo se mueve solo**: el de la línea, el total y el de la
porción.

Cada línea muestra cuánto cuesta y qué porcentaje del plato representa,
porque «qué es lo caro acá» es la pregunta de esa pantalla. Una preparación
se abre en el mismo lugar para ver su desglose.

Si a algún ingrediente le falta precio, el total **no se muestra**. Ni
gris, ni parcial, ni aproximado: un guion, un aviso, y el nombre del
ingrediente que falta con su enlace. Un número en pantalla es un número que
alguien va a usar.

### Histórico

Cuánto costaba este plato hace tres meses. Una foto diaria del costo, con
el método y la ventana que se usaron ese día, en la misma tarjeta que
`restaurant-manager` ya usa para el histórico de precios.

---

## Qué falta

**Todo lo de arriba.** Lo que sí está, hoy:

- El diseño completo, con sus decisiones y el porqué de cada una —
  `PLAN.md`, 29 decisiones numeradas y 8 reglas innegociables.
- El modelo de datos (10 tablas) y el esqueleto que levanta con
  `docker compose up`.
- **La sincronización del catálogo**, funcionando contra el app real: 1 420
  productos y 17 645 compras en 46 segundos, y unos 2 segundos cuando solo
  trae lo que cambió. Se maneja desde Ajustes.
- El motor de costeo: conversión de unidades, los seis métodos, la merma,
  el recorrido del grafo de recetas y la detección de ciclos — con 157
  pruebas, incluidas las feas (ventana vacía, precio sin tipo de cambio,
  una salsa que se contiene a sí misma, unidades incompatibles).

Y después de la fase 1 quedan dos más, ya pensadas pero sin diseñar:

- **Fase 2 — precio de venta y margen.** Cuánto cobrás, cuánto te queda,
  qué porcentaje de food cost, y el precio sugerido a partir de un food
  cost objetivo. Más alertas cuando el costo de un plato se corre.

  El precio del menú **siempre se pone con 13% de IVA y 10% de servicio
  adentro**, los dos sobre la base. O sea que sacarlos es una sola cuenta:
  `precio neto = precio del menú / 1,23`.

  Es 1,23 y no 1,243, y eso quedó confirmado contra 620 días de ventas
  reales: el IVA da **exactamente 13,00% del subtotal menos descuento en
  617 de esos 620 días**, y nunca más. Si el servicio entrara en la base
  del IVA, la relación sería 11,86% — no lo es. El servicio no paga IVA.

  Un cuidado que vale la pena anotar: **esa división no se le aplica a las
  ventas totales.** En el agregado el servicio anda entre 6,24% y 10,00%
  del subtotal según el día (mediana 8,94%), porque parte de la venta no
  lleva servicio — para llevar. El precio se fija parejo; la mezcla de
  ventas no lo es. Para el food cost del restaurante entero se usa la
  columna `Subtotal − Descuento` del archivo, que ya viene limpia.
- **Fase 3 — ingeniería de menú.** Los cuadrantes de popularidad contra
  rentabilidad, y la comparación entre lo que las recetas *dicen* que se
  consumió y lo que el inventario dice que de verdad salió. El POS sí
  exporta ventas por plato, así que ya no está bloqueada; falta definir en
  qué formato viene el archivo y dónde cae. Ojo: esos datos entran acá
  directo, no por `restaurant-manager`, que solo lee totales diarios.

Y aparte de las fases, una cosa que no es una función sino un requisito
para vendérselo a otro restaurante: **usuarios**. Hoy los dos apps asumen
una sola persona, sin login, y lo único que los protege es que no se
alcanzan desde la red. El diseño ya está escrito (`PLAN.md` §16) y cubre
los dos apps con una sola lógica: `restaurant-manager` maneja los usuarios
y `menu-builder` valida la sesión sin tener que preguntarle nada — así, si
el otro app se cae, el costeo sigue abriendo.

La regla que no se negocia: **el login entra en el mismo commit que el
cambio de red.** El día que esto se abra a la red local para que la cocina
lo vea en una tablet, «sin login» deja de ser una simplificación y pasa a
ser una puerta abierta.

---

## Limitaciones conocidas

- **No hay autenticación**, igual que `restaurant-manager`, y por eso está
  amarrado a `127.0.0.1`. No lo expongás a internet.
- **No se convierten gramos a mililitros.** Un litro de agua es un kilo;
  un litro de aceite, de miel o de crema no. Convertir entre peso y volumen
  necesita la densidad del insumo, y hasta que esté escrita el app se
  niega a hacerlo en vez de adivinar.
- **Sin Alembic.** Las tablas se crean solas la primera vez, pero agregar
  una columna a una tabla que ya existe pide un `ALTER TABLE` a mano. Es
  deuda heredada del otro app, tomada a conciencia y anotada como tal.
- **El costo depende de que el catálogo esté al día.** Si la
  sincronización falla, el app no se cae: sigue costeando con lo que tiene
  y muestra de cuándo es. Un número viejo se lee como viejo, no como
  actual.

---

## Documentos

- **`PLAN.md`** — el diseño. Qué se decidió, por qué, y qué queda abierto.
  Leerlo antes de proponer arquitectura.
- **`CLAUDE.md`** — convenciones y reglas para trabajar en este repo.
