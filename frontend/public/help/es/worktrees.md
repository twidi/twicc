Un worktree es una segunda copia de trabajo del mismo repositorio, con una
**rama diferente** y en su **propia carpeta**, que comparte el historial del repositorio. Te
permite a ti (o a un agente) trabajar en una rama de funcionalidad sin tocar tu copia
principal.

En TwiCC, un worktree aparece como **su propio** [proyecto](help/projects),
vinculado automáticamente al repositorio principal. Sus sesiones, costo y actividad **se suman a la vista del
proyecto padre**, así que lo ves todo junto en lugar de separado.

### Dónde se crean los worktrees

El ajuste **directorio de worktrees** decide dónde crea TwiCC los nuevos worktrees
y rellena previamente la ruta cuando creas uno. El global es una **plantilla**
con marcadores que se resuelven por proyecto: `{git_root}` (su raíz de Git),
`{project_name}` (su nombre, o el nombre de la carpeta si no tiene) y `{project_basedir}`
(el nombre de su carpeta). Así, `{git_root}/.worktrees` mantiene los worktrees dentro de cada repositorio,
mientras que `/home/me/worktrees/{project_name}` los reúne todos en un solo lugar. Cada
proyecto puede sobrescribirlo con su propia ruta absoluta: define el global en
Configuración, y el de un proyecto en su cuadro de diálogo de edición.

### Crear uno

Creas un worktree al iniciar una nueva sesión en él. En un menú de **Nueva sesión**,
cada fila de proyecto tiene a su derecha un botón de worktree (una rama con un **+**);
haz clic en él para abrir el cuadro de diálogo de worktree. (El botón solo aparece para
los proyectos que son repositorios Git.)

En el cuadro de diálogo:

- **Worktree nuevo**: TwiCC crea el worktree y su rama en la ubicación
  elegida.
- **Worktree existente**: adopta un worktree que ya está en el disco y que TwiCC aún no
  conoce (la pestaña «Worktree existente»).

En ambos casos, puedes iniciar de inmediato un borrador de sesión en el nuevo worktree.

### Limpiar

Un worktree sigue apareciendo en el selector de proyectos y en los menús de Nueva sesión hasta que
lo **archivas**, desde su cuadro de diálogo de edición o su menú **⋮** en el selector de proyectos
en la parte superior de la barra lateral (archivar lo oculta sin eliminar
nada). Eso solo lo quita de TwiCC: terminar el worktree de verdad
(fusionar la rama y quitarlo del disco) depende de ti o de tu agente;
TwiCC no lo hace.
