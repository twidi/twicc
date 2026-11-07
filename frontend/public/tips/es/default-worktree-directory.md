Indica a TwiCC dónde crear los nuevos worktrees de Git con una **plantilla**. Usa marcadores que se resuelven por proyecto: `{git_root}` (su raíz de Git), `{project_name}` (su nombre, o el nombre de su carpeta si no tiene) y `{project_basedir}` (el nombre de su carpeta). Por ejemplo, `{git_root}/.worktrees` los mantiene dentro del repositorio, mientras que `/home/me/worktrees/{project_name}` reúne en un solo lugar los worktrees de todos los proyectos. El cuadro de diálogo de worktree rellena entonces la ruta a partir de ella; aun así, puedes cambiarla cada vez.

Defínela en **Configuración → General → Plantilla del directorio de worktrees** y sobrescríbela por proyecto (con una ruta absoluta simple) desde el cuadro de diálogo de edición de ese proyecto.

Más en el cuadro de diálogo de ayuda: [Trabajar con worktrees](help/worktrees).
