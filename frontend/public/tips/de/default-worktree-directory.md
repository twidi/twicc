Lege mit einer **Vorlage** fest, wo TwiCC neue Git-Worktrees standardmäßig anlegt.
Nutze Platzhalter, die pro Projekt aufgelöst werden: `{git_root}` (sein Git-Stammverzeichnis),
`{project_name}` (sein Name oder, falls unbenannt, der Ordnername) und
`{project_basedir}` (sein Ordnername). Zum Beispiel hält `{git_root}/.worktrees`
sie im Repository, während `/home/me/worktrees/{project_name}` die Worktrees
aller Projekte an einem Ort sammelt. Der Worktree-Dialog füllt den Pfad dann
daraus vor – du kannst ihn jedes Mal noch ändern.

Stelle sie unter **Einstellungen → Allgemein → Vorlage für das Worktree-Verzeichnis** ein
und überschreibe sie pro Projekt (mit einem einfachen absoluten Pfad) im
Bearbeiten-Dialog des Projekts.

Mehr im Hilfedialog: [Mit Worktrees arbeiten](help/worktrees).
