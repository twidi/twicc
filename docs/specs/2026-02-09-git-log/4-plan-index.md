# GitLog — Plan de portage et structure de fichiers cible

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)

## Documents de la spécification

1. [Contexte et présentation](./1-contexte.md)
2. [Architecture détaillée de la librairie source](./2-architecture-source.md)
3. [Décisions de portage et analyse de portabilité](./3-portage.md)
4. **Plan de portage et structure de fichiers cible** (ce document)

---

## 14. Plan de portage

> **Mode de développement** : Chaque sous-phase est une tâche autonome confiée à un agent de développement. L'agent reçoit cette spec comme contexte, effectue la sous-phase, et le résultat est validé avant de passer à la suivante. Les sous-phases au sein d'une même phase sont séquentielles (chacune dépend de la précédente). Les phases elles-mêmes sont largement séquentielles aussi, sauf indication contraire.

Le plan est découpé en fichiers séparés par phase pour faciliter le travail des agents :

- [Phase 0 : Préparation de l'environnement TypeScript](./4-plan-phase-0.md)
- [Phase 1 : Copier le TypeScript pur](./4-plan-phase-1.md)
- [Phase 2 : Créer les composables Vue](./4-plan-phase-2.md)
- [Phase 3 : Composant racine + Layout](./4-plan-phase-3.md)
- [Phase 4 : Module Graph (HTML Grid)](./4-plan-phase-4.md)
- [Phase 5 : Module Table](./4-plan-phase-5.md)
- [Phase 6 : Module Tags](./4-plan-phase-6.md)
- [Phase 7 : ~~Intégration tooltips WebAwesome~~ SUPPRIMÉE](./4-plan-phase-7.md)
- [Phase 8 : Finalisation, vue de test et validation visuelle](./4-plan-phase-8.md)

---

## 15. Structure de fichiers cible

```
src/
  components/
    GitLog/
      index.ts                          # Export public
      types.ts                          # GitLogEntry, Commit, GitLogCommonProps, props, etc.
      constants.ts                      # ROW_HEIGHT, DEFAULT_NODE_SIZE, etc.

      # --- Algorithmes TS pur (copiés quasi tels quels) ---
      data/
        index.ts                        # Réexport
        GraphDataBuilder.ts
        ActiveBranches.ts
        ActiveNodes.ts
        computeRelationships.ts
        temporalTopologicalSort.ts
        types.ts

      # --- Composables Vue (adaptés des hooks React) ---
      composables/
        keys.ts                         # InjectionKey pour les 4 contextes + types des bags
        useGitContext.ts                # provide/inject
        useThemeContext.ts
        useGraphContext.ts
        useTableContext.ts
        useTheme.ts                     # couleurs, palettes (11 fonctions/valeurs)
        useSelectCommit.ts              # sélection/preview (consomme useGitContext)
        useColumnData.ts                # calcul matrice colonnes
        usePlaceholderData.ts           # données de placeholder pour skeleton
        useResize.ts                    # redimensionnement du graphe

      # --- Composant principal ---
      GitLog.vue                        # provide les contextes, layout
      Layout.vue                        # Mise en page flex (tags | graph | table)
      Layout.module.scss

      # --- Module Graph (HTML Grid seulement) ---
      graph/
        GraphCore.vue
        GraphCore.module.scss
        HTMLGridGraph.vue               # (pas de .module.scss propre, utilise GraphCore.module.scss)
        constants.ts                    # GRAPH_MARGIN_TOP = 12
        placeholderData.ts              # Données statiques de placeholder (commits factices)
        GraphMatrixBuilder/             # TS pur, copié tel quel
          index.ts                      # Réexport
          GraphMatrixBuilder.ts
          GraphMatrix.ts
          GraphMatrixColumns.ts
          GraphEdgeRenderer.ts
          BranchingEdgeRenderer.ts
          VirtualEdgeRenderer.ts
          types.ts
        utils/
          getColumnBackgroundSize.ts    # TS pur
          getMergeNodeInnerSize.ts      # TS pur
          isColumnEmpty.ts              # TS pur (vérifie si une colonne est vide)
          getEmptyColumnState.ts        # TS pur (retourne l'état par défaut d'une colonne vide)
        components/
          GraphRow.vue
          GraphColumn.vue
          GraphColumn.module.scss
          CommitNode.vue
          CommitNode.module.scss
          CommitNodeTooltip.vue          # Tooltip par défaut des nœuds de commit
          CommitNodeTooltip.module.scss
          VerticalLine.vue
          VerticalLine.module.scss
          HorizontalLine.vue
          HorizontalLine.module.scss
          CurvedEdge.vue                # SVG paths
          CurvedEdge.module.scss
          LeftDownCurve.vue
          LeftDownCurve.module.scss
          LeftUpCurve.vue
          LeftUpCurve.module.scss
          ColumnBackground.vue
          ColumnBackground.module.scss
          BreakPoint.vue
          BreakPoint.module.scss
          HeadCommitVerticalLine.vue
          HeadCommitVerticalLine.module.scss
          IndexPseudoCommitNode.vue
          IndexPseudoCommitNode.module.scss
          IndexPseudoRow.vue
          SkeletonGraph.vue

      # --- Module Table ---
      table/
        GitLogTable.vue
        GitLogTable.module.scss
        constants.ts                    # HEADER_ROW_HEIGHT = 47, TABLE_MARGIN_TOP = 12
        components/
          TableContainer.vue
          TableContainer.module.scss
          TableRow.vue
          TableRow.module.scss
          CommitMessageData.vue
          CommitMessageData.module.scss
          AuthorData.vue
          AuthorData.module.scss
          TimestampData.vue
          TimestampData.module.scss
          IndexStatus.vue
          IndexStatus.module.scss

      # --- Module Tags ---
      tags/
        GitLogTags.vue
        GitLogTags.module.scss
        components/
          BranchTag.vue
          BranchTag.module.scss
          BranchTagTooltip.vue
          BranchTagTooltip.module.scss
          BranchLabel.vue
          BranchLabel.module.scss
          BranchIcon.vue
          BranchIcon.module.scss
          TagIcon.vue
          TagIcon.module.scss
          TagLabel.vue
          TagLabel.module.scss
          GitIcon.vue
          GitIcon.module.scss
          IndexLabel.vue
          IndexLabel.module.scss
          Link.vue
          Link.module.scss

      # --- Utilitaires ---
      utils/
        createRainbowTheme.ts           # TS pur
        formatBranch.ts                  # TS pur
        colors.ts                        # palettes neon-aurora, etc.

      # --- Assets ---
      assets/
        branch.svg
        git.svg
        merge.svg
        tag.svg
        minus.svg
        pencil.svg
        plus.svg
```

**Principes de cette structure** :
- Les **SCSS modules sont scopés par composant** — chaque composant qui a un `.module.scss` dans le source en a un dans la cible
- Le dossier `data/` et `GraphMatrixBuilder/` sont des **copies directes** du repo original
- Les composables Vue regroupés dans `composables/` pour la lisibilité
- Les sous-composants de chaque module regroupés dans `components/`
- Un seul composant racine `GitLog.vue` (pas de `GitLogPaged` séparé)
- `HTMLGridGraph.vue` n'a **pas** de `.module.scss` propre (il utilise `GraphCore.module.scss`)

**Usage attendu du composant porté** :

```vue
<template>
  <GitLog :entries="entries" current-branch="main" theme="dark" colours="neon-aurora-dark">
    <template #tags>
      <GitLogTags />
    </template>
    <template #graph>
      <GitLogGraphHTMLGrid :node-size="20" :orientation="'normal'" />
    </template>
    <template #table>
      <GitLogTable timestamp-format="YYYY-MM-DD HH:mm" />
    </template>
  </GitLog>
</template>
```
