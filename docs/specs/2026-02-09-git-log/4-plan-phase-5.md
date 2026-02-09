# GitLog — Plan de portage — Phase 5

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 5 : Module Table

#### Phase 5.1 : GitLogTable.vue et TableRow.vue (structure)

**Entrée** : `modules/Table/Table.tsx`, `modules/Table/components/TableContainer/`, `modules/Table/components/TableRow/` + SCSS + contexte
**Sortie** : `table/GitLogTable.vue`, `table/components/TableRow.vue` + SCSS

- `GitLogTable.vue` :
  - `provide(TABLE_CONTEXT_KEY, { timestampFormat })` pour le format de date (défaut: `'YYYY-MM-DD HH:mm:ss'`)
  - Conteneur principal qui wrape `TableContainer`
  - Props : `timestampFormat`, `className`, `styles` (objet `GitLogTableStylingProps` avec `table`, `thead`, `tr`, `td` en CSSProperties)
  - Supporte le scoped slot `#row` pour le custom render de ligne complète
  - Applique `TABLE_MARGIN_TOP` (12px) et `HEADER_ROW_HEIGHT` (47px) pour l'alignement avec le graphe
  - **Note importante sur les en-têtes** : Les en-têtes du tableau ("Commit message", "Author", "Timestamp") sont rendus **dans le composant `Table`** lui-même (dans le code source React, `Table.tsx` lignes 50-83), et non dans le `Layout`. C'est différent des en-têtes "Branch / Tag" et "Graph" qui eux sont rendus dans `Layout.tsx`. Le composant `GitLogTable.vue` devra reproduire ce comportement
  - **Note** : `Table.tsx` utilise le hook `usePlaceholderData()` pour afficher des lignes de table placeholder quand il n'y a pas de données. Le graphe (`HTMLGridGraph.tsx`) utilise aussi des données de placeholder, mais via un import direct du fichier `data.ts` (pas via le hook)
  - **Note sur le filtrage** : Contrairement à `GraphCore.tsx` et `Tags.tsx`, `Table.tsx` ne réapplique **PAS** le filtre sur les commits. Il utilise directement les commits de `graphData` (déjà filtrés dans `GitLogCore`) slicés par la pagination. C'est un comportement cohérent mais distinct des deux autres modules.
- `TableContainer.vue` :
  - Composant intermédiaire (existe dans le source avec son propre SCSS module `TableContainer.module.scss`)
  - Itère sur les commits visibles et rend un `TableRow` par commit
- `TableRow.vue` :
  - Reçoit un `commit` en prop + `selected`, `previewed`, `backgroundColour`
  - Si le scoped slot `#row` est fourni : rend le slot avec les données exposées
  - Sinon : rend le layout par défaut (message | auteur | date)
  - Événements : `@click` → sélection, `@mouseenter`/`@mouseleave` → preview
  - **Note** : Pas de `cloneElement` — les handlers sont sur le wrapper `<div>` du row, pas injectés dans le slot

**Critère de validation** : Le tableau est rendu avec les bonnes lignes. Le scoped slot fonctionne.

#### Phase 5.2 : Sous-composants de cellules (données)

**Entrée** : `CommitMessageData`, `AuthorData`, `TimestampData`, `IndexStatus` (composants React)
**Sortie** : Composants Vue correspondants dans `table/components/`

- `CommitMessageData.vue` : Affiche le message du commit. Tronque si trop long. Affiche le hash en résumé.
- `AuthorData.vue` : Affiche le nom de l'auteur (et optionnellement l'email).
- `TimestampData.vue` : Affiche la date avec le format configurable (via `useTableContext` → `timestampFormat`). Utilise `dayjs` pour le formatage (relatif ou absolu).
- `IndexStatus.vue` : Affiche les compteurs du working directory : nombre de fichiers modifiés/ajoutés/supprimés avec icônes (+, ~, -).

**Critère de validation** : Chaque cellule affiche la bonne donnée avec le bon formatage.

#### Phase 5.3 : Interactions de la table (sélection, preview, synchronisation)

**Entrée** : Composants de la phase 5.1 + contexte Git
**Sortie** : Mise à jour des composants pour les interactions

- Synchroniser la sélection/preview avec le graphe : la table et le graphe partagent le même `selectedCommit` et `previewedCommit` via le `GitContext`
- Hover sur une ligne → preview dans le graphe (et inversement)
- Clic sur une ligne → sélection (et inversement)
- Fond coloré (gradient linéaire) basé sur la couleur de colonne du commit dans le graphe

**Critère de validation** : Hover sur la table met en évidence la ligne correspondante dans le graphe. Clic dans l'un sélectionne dans l'autre.

---

## Tasks tracking

- [ ] Phase 5.1 : GitLogTable.vue et TableRow.vue (structure)
- [ ] Phase 5.2 : Sous-composants de cellules (données)
- [ ] Phase 5.3 : Interactions de la table (sélection, preview, synchronisation)
