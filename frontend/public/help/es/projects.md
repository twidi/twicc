Un proyecto es un **directorio de trabajo** donde se ejecutan sesiones de agente: un proyecto por
carpeta. TwiCC crea uno automáticamente para cada carpeta que tiene sesiones,
incluidas las sesiones que ejecutaste con Claude Code o Codex **fuera** de TwiCC. También
puedes añadir un proyecto tú mismo indicándole una carpeta a TwiCC.

### Qué conserva TwiCC bajo un proyecto

Todas las sesiones que se ejecutan en esa carpeta se agrupan bajo el proyecto, así que
puedes encontrar tu trabajo según dónde ocurrió.

### Abrir un proyecto

Abre la **página de inicio** de un proyecto haciendo clic en él en la página de inicio o en el
selector de proyectos en la parte superior de la barra lateral, incluso si ya está seleccionado. Desde dentro de una
sesión, el nombre del proyecto en el encabezado te lleva de vuelta a él.

Para renombrar un proyecto o cambiar su configuración, abre su menú **⋮** (en la página de inicio o en el
selector de proyectos) y elige **Editar**.

### Hazlo tuyo

Dale a un proyecto un **nombre** y un **color**, y define **valores predeterminados por proyecto**
(proveedor, configuración del agente, diseño predeterminado) que se aplican a cada nueva sesión que inicies
allí.

Poner un nombre también ordena la lista de la página de inicio: los proyectos **con nombre** aparecen arriba como tarjetas, para que
los importantes sean fáciles de alcanzar, mientras que los **sin nombre** se listan debajo,
agrupados en un árbol de carpetas según sus rutas.

### Organizar

Agrupa proyectos relacionados en un [espacio de trabajo](help/workspaces). Y los
[worktrees](help/worktrees) de un repositorio Git aparecen como proyectos propios, vinculados a él,
así que al abrir un proyecto también se listan las sesiones de sus worktrees, con una insignia para distinguir
las sesiones del proyecto padre de las de los worktrees.

### Archivar

¿Terminaste con un proyecto? **Archívalo**: se oculta de las listas, pero no se elimina nada
y sus sesiones siguen siendo localizables con la búsqueda. Archívalo desde su cuadro de diálogo de edición o
desde su menú **⋮** en el selector de proyectos en la parte superior de la barra lateral. Para volver a ver los
proyectos archivados, marca **Mostrar archivados** en la sección Proyectos de la página de inicio;
desde ahí puedes **desarchivar** cualquiera de ellos.
