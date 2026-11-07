El selector de modelo es una **matriz**: una fila por modelo y una columna por esfuerzo de
razonamiento. Cada celda seleccionable muestra una **puntuación de 0 a 100**:
qué tan bien encaja ese modelo, con ese esfuerzo, en la tarea que describes con los
controles bajo la matriz. Cuanto más alta, mejor. Un **?** discreto significa que no hay
datos utilizables para ese par.

La matriz incluye algunas señales visuales:

- el **fondo** de la celda se colorea más a medida que sube la puntuación;
- una **marca de verificación** señala la celda seleccionada actualmente y un pequeño **punto** la
  predeterminada;
- un **anillo** resalta la mejor puntuación de cada proveedor: continuo para tu
  proveedor predeterminado, discontinuo para los demás cuando se muestran varios proveedores.

## De dónde vienen los datos

Las puntuaciones se construyen a partir de las evaluaciones publicadas por
**[Artificial Analysis](https://artificialanalysis.ai/)**, un sitio independiente de
benchmarks. Mide cada modelo, con cada esfuerzo de razonamiento, en
muchas evaluaciones, junto con el **costo** y el **tiempo** de cada tarea.

TwiCC incluye una instantánea de estos resultados tomada el **2026-10-29**, con
la versión 5.0 del Intelligence Index. No se actualiza automáticamente.

## Los controles

- **Tipo de tarea** — qué clase de trabajo vas a hacer (ver más abajo).
  *General* usa el Intelligence Index global de Artificial Analysis.
- **Dificultad de la tarea** — qué tan capaz debe ser el modelo. Sube el control para una
  tarea difícil y bájalo para una fácil.
- **Priorizar** — **Costo** para preferir los pares más baratos, **Velocidad** para preferir los más
  rápidos.
- **Seleccionar automáticamente la mejor opción** — selecciona por ti la celda con mejor puntuación cada vez que
  cambia un control, exactamente como si hicieras clic en ella. Cuando se muestran varios proveedores,
  **Solo el proveedor predeterminado** limita esa elección a tu proveedor
  predeterminado.
- **Seleccionar automáticamente la mejor opción** y **Mostrar modelos antiguos** se desactivan cada vez que
  se abre o se cierra el selector, el panel de configuración o un formulario de ajuste predefinido.

## Qué significa la puntuación

La dificultad fija un **nivel objetivo** en la escala de capacidad del tipo de tarea.
Un par **igual o superior** al objetivo cumple el trabajo: entre ellos, gana el más barato
(o el más rápido). Un par ligeramente **por debajo** del objetivo pierde algunos
puntos; un par muy por debajo pierde tantos que un precio bajo no lo salva.

- **100** es el mejor par para esta tarea.
- **50** es "una duplicación peor": el doble de costo (o de tiempo) que el mejor,
  o estar tan por debajo del objetivo que cuenta lo mismo.
- Los pares más alejados conservan puntuaciones **pequeñas pero distintas de cero**. Varios de ellos
  pueden mostrar el mismo número: el **anillo** sigue marcando el mejor par de cada proveedor,
  así que puedes elegir el mejor de un solo proveedor.
- Las puntuaciones comparan **todos los proveedores activados** a la vez, así que puedes elegir
  entre Claude Code y Codex. Los proveedores desactivados, los modelos retirados o desactivados
  y los esfuerzos que un modelo no admite nunca cuentan.
- La instantánea cubre **solo los modelos más recientes**: la versión 5.0 del
  Intelligence Index no mide los anteriores. Con **Mostrar modelos antiguos**
  activado, sus celdas muestran **?**.

## Tipos de tarea

| Tipo de tarea | Basado en |
|---|---|
| General | Intelligence Index (la puntuación global de Artificial Analysis); costo y tiempo de Terminal-Bench 4.0 |
| Programación y terminal | Terminal-Bench 4.0 (programación con agentes en una terminal), FrontierCode 2.0 (programación) |
| Oficina y trabajo del conocimiento | AA-Briefcase (entregables de trabajo del conocimiento), GDP.pdf (razonamiento sobre documentos) |
| Automatización y herramientas | AutomationBench-AA (flujos de trabajo SaaS con herramientas) |
| Ciencia y razonamiento | HLE Diamond (razonamiento difícil), CritPt v2 Hard (física), Terminal-Bench-Science (trabajo científico en una terminal) |
| Conocimiento factual | AA-Omniscience (conocimiento y alucinaciones) |

Para un tipo basado en varias evaluaciones, la capacidad es su promedio
(cada una en una escala de 0 a 100), y lo mismo ocurre con el costo y el tiempo.

## Bajo el capó (para los curiosos)

- El control deslizante asigna **0** al par más débil y **100** al más fuerte
  para el tipo de tarea elegido, con una curva que sube rápido al principio: la
  mitad del control ya exige un modelo capaz.
- **Sin penalización por encima del objetivo**: un par que lo supera solo paga
  su costo (o su tiempo).
- Estar **2 puntos del Intelligence Index** por debajo del objetivo pesa tanto como
  pagar el doble; los demás tipos de tarea escalan esto según su propio rango.
  La penalización crece con la **cuarta potencia** de la diferencia: 4 puntos por debajo
  pesan tanto como pagar 2¹⁶ veces más.
- La puntuación baja de 100 a 50 en la primera duplicación, y luego cada vez
  más despacio, así que nunca se desploma a 0.
- El costo y el tiempo se comparan en una escala **logarítmica**: lo que importa es
  la proporción ($1 a $2 cuenta igual que $10 a $20).
- El tiempo es el tiempo por tarea de Artificial Analysis: el tiempo dedicado a generar
  la respuesta, sin retrasos de arranque.
