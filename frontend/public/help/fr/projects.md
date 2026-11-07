Un projet est un **répertoire de travail** où s'exécutent des sessions d'agent — un projet par
dossier. TwiCC en crée un automatiquement pour chaque dossier contenant des sessions, y compris
celles que vous avez lancées avec Claude Code ou Codex **en dehors** de TwiCC. Vous pouvez aussi
ajouter un projet vous-même en indiquant un dossier à TwiCC.

### Ce que TwiCC conserve sous un projet

Toutes les sessions exécutées dans ce dossier sont regroupées sous le projet. Vous retrouvez
ainsi votre travail selon l'endroit où il a eu lieu.

### Ouvrir un projet

Ouvrez l'**accueil** d'un projet en cliquant dessus sur la page d'accueil, ou dans le sélecteur
de projet en haut de la barre latérale — même celui qui est déjà sélectionné. Depuis une session,
le nom du projet dans l'en-tête vous y ramène.

Pour renommer un projet ou modifier ses paramètres, ouvrez son menu **⋮** (sur l'accueil ou
dans le sélecteur de projet) et choisissez **Modifier**.

### Personnalisez-le

Donnez à un projet un **nom** et une **couleur**, puis définissez des **valeurs par défaut par
projet** (fournisseur, paramètres d'agent, disposition par défaut) qui initialisent chaque
nouvelle session que vous y démarrez.

Le nom détermine aussi l'ordre de la liste d'accueil : les projets **nommés** sont en haut sous
forme de cartes, pour que les plus importants soient faciles à atteindre. Les projets **sans nom**
sont listés dessous, regroupés dans une arborescence de dossiers selon leurs chemins.

### Organiser

Regroupez des projets liés dans un [espace de travail](help/workspaces). Les
[worktrees](help/worktrees) d'un dépôt git apparaissent comme leurs propres projets, reliés au
dépôt. Ouvrir un projet liste donc aussi les sessions de ses worktrees, avec un badge pour
distinguer les sessions du parent de celles des worktrees.

### Archivage

Vous en avez fini avec un projet ? **Archivez**-le. Il est masqué des listes, mais rien n'est
supprimé et ses sessions restent consultables par la recherche. Archivez-le depuis sa boîte de
dialogue de modification, ou depuis son menu **⋮** dans le sélecteur de projet en haut de la
barre latérale. Pour revoir les projets archivés, cochez **Afficher les archivés** dans la
section Projets de l'accueil. Vous pouvez alors **désarchiver** n'importe lequel d'entre eux.
