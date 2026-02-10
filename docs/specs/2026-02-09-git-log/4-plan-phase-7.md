# GitLog — Plan de portage — Phase 7

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 7 : Intégration tooltips WebAwesome

#### Phase 7.1 : Audit et remplacement des tooltips

**Entrée** : Tous les composants des phases 4-6 qui utilisent des tooltips
**Sortie** : Intégration unifiée avec les composants WebAwesome

- Recenser tous les usages de tooltip :
  - `CommitNode` → tooltip au survol du nœud (scoped slot `#tooltip` ou défaut). **Config source** : `positions={['top', 'bottom']}`, `padding={0}`, `ArrowContainer` avec flèche positionnée, `z-index: 20` sur le contenu.
  - `BranchTag` → tooltip au survol du badge de branche (composant `BranchTagTooltip`). **Config source** : `positions='right'` — positionnement à droite, différent de celui du `CommitNode`.
- Remplacer par les composants WebAwesome :
  - Identifier le composant WebAwesome approprié (popover, tooltip, ou autre)
  - Adapter les props (position, trigger, contenu) en reproduisant les positionnements distincts : top/bottom pour `CommitNode`, right pour `BranchTag`
  - Reproduire le `z-index: 20` et la flèche du `CommitNode` tooltip
  - S'assurer que le scoped slot `#tooltip` du graphe fonctionne toujours avec le wrapper WebAwesome
  - **Note (phase 1.1 décision #3)** : Le scoped slot `#tooltip` est typé avec `CustomTooltipProps` (de `types.ts`) qui expose `commit`, `borderColour`, `backgroundColour`. Le type function React `CustomTooltip` n'a pas été porté. De même, le slot `#node` est typé avec `CustomCommitNodeProps` (de `types.ts`).

**Critère de validation** : Les tooltips s'affichent correctement au survol, avec le bon contenu et le bon positionnement.

#### Phase 7.2 : Tooltip par défaut du commit

**Entrée** : `CommitNodeTooltip` React (contenu par défaut quand aucun custom tooltip n'est fourni)
**Sortie** : Composant Vue ou template par défaut

- Créer le contenu par défaut du tooltip de commit : hash, message, auteur, date, branche
- Styliser avec les couleurs du thème (`borderColour`, `backgroundColour`)
- Ce contenu est utilisé quand le scoped slot `#tooltip` n'est pas fourni

**Critère de validation** : Le tooltip par défaut affiche les informations du commit avec le bon style.

---

## Tasks tracking

- [ ] Phase 7.1 : Audit et remplacement des tooltips
- [ ] Phase 7.2 : Tooltip par défaut du commit
