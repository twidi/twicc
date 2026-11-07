Indiquez à TwiCC où placer les nouveaux worktrees git avec un **modèle**. Utilisez des variables
résolues par projet : `{git_root}` (sa racine git), `{project_name}` (son
nom, ou le nom de son dossier s'il n'a pas de nom) et `{project_basedir}` (le nom de son dossier). Par
exemple, `{git_root}/.worktrees` les garde dans le dépôt, tandis que
`/home/me/worktrees/{project_name}` rassemble les worktrees de tous les projets au même
endroit. La boîte de dialogue du worktree préremplit ensuite le chemin à partir de ce modèle ; vous pouvez toujours
le modifier à chaque fois.

Définissez-le dans **Paramètres → Général → Modèle de répertoire des worktrees**, et remplacez-le
par projet (avec un simple chemin absolu) depuis la boîte de dialogue de modification du projet.

Plus de détails dans la boîte de dialogue d'aide : [Travailler avec les worktrees](help/worktrees).
