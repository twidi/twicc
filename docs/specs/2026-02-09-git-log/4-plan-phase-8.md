# GitLog — Plan de portage — Phase 8

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 8 : Finalisation, vue de test et validation visuelle

> **Note** : La partie "data source" (endpoint back-end, intégration API réelle) est hors scope pour l'instant. Cette phase se concentre uniquement sur le composant pur, alimenté par des données simulées (fake data).

#### Phase 8.0 : Démarrage des serveurs de développement dans le worktree

**Entrée** : Worktree `/home/twidi/dev/twicc-poc-git-graph/` sur la branche `feature/git-graph-visualization`
**Sortie** : Serveurs backend et frontend fonctionnels dans le worktree

Le développement se fait dans un **worktree Git** séparé du repo principal. Les serveurs de développement doivent tourner avec leurs propres ports et leur propre base de données.

- Copier la base de données depuis le worktree principal (si elle n'existe pas déjà dans le worktree) :
  ```bash
  cp /home/twidi/dev/twicc-poc/data.sqlite* /home/twidi/dev/twicc-poc-git-graph/
  ```
- Configurer les ports dans le fichier `.env` à la racine du worktree (si le fichier n'existe pas déjà) :
  - Trouver des ports disponibles sur le système pour le frontend et le backend
  - Créer le fichier `.env` :
    ```env
    # Backend port (Uvicorn server)
    TWICC_PORT=<port_backend_disponible>

    # Frontend port (Vite dev server)
    VITE_PORT=<port_frontend_disponible>
    ```
- Démarrer les serveurs via le contrôleur de développement :
  ```bash
  cd /home/twidi/dev/twicc-poc-git-graph
  uv run ./devctl.py start all
  ```
- Vérifier que les serveurs sont bien lancés :
  ```bash
  uv run ./devctl.py status
  ```
- En cas de problème, consulter les logs :
  ```bash
  uv run ./devctl.py logs front
  uv run ./devctl.py logs back
  ```

**Critère de validation** : `uv run ./devctl.py status` montre les deux serveurs en cours d'exécution. Le frontend est accessible dans le navigateur à l'URL configurée.

#### Phase 8.1 : Export public et API du composant

**Entrée** : Tous les composants des phases 1-7
**Sortie** : `src/components/GitLog/index.ts` finalisé

- Exporter les composants publics : `GitLog`, `GitLogGraphHTMLGrid`, `GitLogTable`, `GitLogTags`
- Exporter les types publics : `GitLogEntry`, `Commit`, `GitLogProps`, `CommitFilter`, `GitLogPaging`, `GitLogIndexStatus`, etc.
- Exporter les composables publics si pertinent : `useGitContext`, `useThemeContext`
- Documenter les scoped slots disponibles dans un commentaire ou type

**Critère de validation** : `import { GitLog, GitLogGraphHTMLGrid, GitLogTable, GitLogTags } from '@/components/GitLog'` fonctionne.

#### Phase 8.2 : Données de test simulées (fake git history)

**Entrée** : Types `GitLogEntry`, `GitLogIndexStatus`
**Sortie** : `src/components/GitLog/__tests__/fakeData.ts` (ou emplacement similaire)

- Créer un jeu de données `GitLogEntry[]` simulé représentant un historique git réaliste et suffisamment complexe pour exercer toutes les features du composant :
  - **Branche principale** (`refs/heads/main`) avec une dizaine de commits linéaires
  - **Feature branches** : au moins 2-3 branches (`refs/heads/feature/auth`, `refs/heads/feature/dashboard`, `refs/heads/fix/login-bug`) qui divergent de main à différents points
  - **Merge commits** : au moins 2 merges de feature branches vers main (commits avec 2 parents)
  - **Tags** : au moins 2-3 tags (`refs/tags/v1.0.0`, `refs/tags/v1.1.0`, `refs/tags/v2.0.0-beta`) placés sur des commits spécifiques
  - **Branches concurrentes** : au moins un moment où 3+ branches coexistent (pour tester la largeur du graphe et les colonnes multiples)
  - **Branche distante** : au moins une branche au format `refs/remotes/origin/feature/...`
  - **Historique suffisamment long** : 30-50 commits au total pour avoir un graphe intéressant visuellement
  - **Auteurs variés** : 3-4 auteurs différents avec noms et emails
  - **Dates réalistes** : espacées sur quelques semaines, en ordre chronologique cohérent
- Créer un objet `GitLogIndexStatus` simulé (`{ modified: 3, added: 1, deleted: 0 }`)
- Exporter une fonction ou constante prête à être consommée par la vue de test
- **Note** : Le code source contient des données de test existantes dans `_test/data/sleep/` (`sleepCommits.ts`, `sleepState.ts`) et un parser `gitLogParser.ts` qui pourraient servir de base ou de référence pour ces fake data

**Critère de validation** : Les données sont cohérentes (les parents référencent des hashes existants, les branches pointent vers les bons commits, le graphe est visuellement intéressant avec merges et branches parallèles).

#### Phase 8.3 : Vue de test indépendante (page dédiée)

**Entrée** : Composant exporté (phase 8.1) + données simulées (phase 8.2)
**Sortie** : Une vue/route Vue dédiée, accessible dans l'app, qui sert de page de démo/test

- Créer une vue Vue indépendante (ex: `src/views/GitLogTestView.vue` ou emplacement adapté au projet)
- Ajouter la route correspondante dans le router du projet
- La vue intègre le composant `<GitLog>` avec les 3 sous-composants (`GitLogTags`, `GitLogGraphHTMLGrid`, `GitLogTable`), alimenté par les fake data de la phase 8.2
- La vue doit exercer **toutes les features** du composant :
  - Affichage du graphe complet avec branches, merges, tags
  - Pseudo-commit index visible (avec `indexStatus`)
  - Sélection d'un commit au clic (avec callback qui affiche le commit sélectionné, ex: dans un panel ou console)
  - Preview au hover
  - Filtrage : un input texte qui filtre les commits par message
  - Resize du graphe (drag handle fonctionnel)
  - Toggle dark/light mode
  - Choix de palette de couleurs (dropdown ou boutons pour switcher entre les palettes)
  - Pagination : contrôles pour changer la page/taille
  - Orientation : toggle normal/flipped
- La page doit être autonome, sans dépendance sur un backend

**Critère de validation** : La page s'affiche dans le navigateur. Le composant GitLog rend le graphe avec les données simulées. Toutes les features listées sont accessibles et manipulables.

#### Phase 8.4 : Validation visuelle dans le navigateur (MCP Chrome)

**Entrée** : Vue de test fonctionnelle (phase 8.3) + app en cours d'exécution
**Sortie** : Rapport de validation visuelle

- L'agent ouvre la page de test dans le navigateur via MCP Chrome
- Prend des screenshots du rendu et vérifie visuellement :
  - Le graphe est rendu avec des nœuds, lignes, courbes visibles
  - Les branches et tags s'affichent dans la colonne de gauche
  - Le tableau (message, auteur, date) s'affiche à droite
  - Les couleurs sont appliquées correctement (une couleur par colonne)
  - Le pseudo-commit index apparaît en haut avec une bordure pointillée
- Teste les interactions :
  - Clic sur un commit → mise en évidence dans le graphe et la table
  - Hover sur une ligne → preview visible
  - Tape dans le champ de filtre → commits filtrés, breakpoints visibles
  - Drag le handle de resize → la largeur du graphe change
  - Toggle dark/light → les couleurs s'adaptent
  - Change de palette → les couleurs du graphe changent
- Identifie les problèmes visuels éventuels (alignements, couleurs manquantes, éléments mal positionnés)
- Produit un rapport avec les screenshots et les problèmes trouvés pour validation par l'utilisateur

**Critère de validation** : Le rendu visuel est cohérent et comparable au Storybook de la lib originale. Les interactions fonctionnent correctement. Les éventuels problèmes sont documentés.

---

## Tasks tracking

- [ ] Phase 8.0: Démarrage des serveurs de développement dans le worktree
- [ ] Phase 8.1: Export public et API du composant
- [ ] Phase 8.2: Données de test simulées (fake git history)
- [ ] Phase 8.3: Vue de test indépendante (page dédiée)
- [ ] Phase 8.4: Validation visuelle dans le navigateur (MCP Chrome)
