Un worktree est une seconde copie de travail du même dépôt, en checkout sur une **autre branche**,
dans son **propre dossier** — tout en partageant l'historique du dépôt. Il vous permet (à vous ou
à un agent) de travailler sur une branche de fonctionnalité sans toucher à votre checkout
principal.

Dans TwiCC, un worktree apparaît comme **son propre** [projet](help/projects), automatiquement
relié au dépôt principal. Ses sessions, son coût et son activité **remontent dans la vue du projet
parent**, pour que vous voyiez tout ensemble au lieu de tout cloisonner.

### Où les worktrees sont créés

Le paramètre **répertoire des worktrees** détermine où TwiCC crée les nouveaux worktrees, et
préremplit le chemin lorsque vous en créez un. Le paramètre global est un **modèle** avec des
variables résolues par projet — `{git_root}` (sa racine git), `{project_name}` (son nom, ou le nom
du dossier s'il n'en a pas) et `{project_basedir}` (le nom de son dossier). Ainsi,
`{git_root}/.worktrees` garde les worktrees dans chaque dépôt, tandis que
`/home/me/worktrees/{project_name}` les rassemble tous au même endroit. Chaque projet peut le
remplacer par son propre chemin absolu — définissez le modèle global dans les Paramètres, et
celui d'un projet dans sa boîte de dialogue de modification.

### En créer un

Vous créez un worktree en démarrant une nouvelle session dedans. Dans un menu **Nouvelle
session**, chaque ligne de projet comporte à sa droite un bouton de worktree — une branche avec
un **+** ; cliquez dessus pour ouvrir la boîte de dialogue du worktree. (Le bouton n'apparaît que
pour les projets qui sont des dépôts git.)

Dans la boîte de dialogue :

- **Nouveau worktree** — TwiCC crée le worktree et sa branche à l'emplacement choisi.
- **Worktree existant** — adoptez un worktree déjà présent sur le disque que TwiCC ne connaît
  pas encore (l'onglet « Worktree existant »).

Dans les deux cas, vous pouvez démarrer tout de suite un brouillon de session dans le nouveau
worktree.

### Nettoyer

Un worktree continue d'apparaître dans le sélecteur de projet et dans les menus Nouvelle session
tant que vous ne l'**archivez** pas — depuis sa boîte de dialogue de modification, ou son menu
**⋮** dans le sélecteur de projet en haut de la barre latérale (archiver le masque sans rien
supprimer). Cela ne fait que le ranger hors de TwiCC : mettre réellement fin au worktree — fusionner
la branche et le supprimer sur le disque — vous revient, à vous ou à votre agent ; TwiCC ne le fait
pas.
