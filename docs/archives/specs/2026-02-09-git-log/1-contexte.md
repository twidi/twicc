# GitLog — Port de @tomplum/react-git-log vers Vue 3

**Date** : 2026-02-09
**Statut** : Spécification validée, prêt pour implémentation

## Documents de la spécification

1. **Contexte et présentation** (ce document)
2. [Architecture détaillée de la librairie source](./2-architecture-source.md)
3. [Décisions de portage et analyse de portabilité](./3-portage.md)
4. [Plan de portage et structure de fichiers cible](./4-plan-index.md)

---

## 1. Contexte et choix de la librairie

### Besoin

Nous avons besoin d'un composant de visualisation d'arbre Git dans notre application Vue.js. Le rendu doit être un graphe vertical classique, avec des nœuds colorés pour les commits, des lignes pour les branches, des courbes pour les merges — similaire à ce qu'on voit dans les GUI Git (GitKraken, VS Code Git Graph, etc.).

Exigences clés :
- Représentation graphique verticale avec nœuds colorés et lignes de branches
- Capacité à ne représenter qu'une partie de l'arbre (pagination / virtual scroll pour de gros historiques)
- Compatible avec Vue.js (directement ou via adaptation)
- Permettre le clic sur les commits pour voir les diffs
- Visualisation du working directory (fichiers non commités)

### Librairie retenue : `@tomplum/react-git-log`

| Info | Valeur |
|------|--------|
| **npm** | `@tomplum/react-git-log` |
| **GitHub** | https://github.com/TomPlum/react-git-log |
| **Auteur** | Thomas Plumpton |
| **Version** | 3.5.1 |
| **Licence** | Apache-2.0 |
| **Framework** | React (TypeScript) |
| **Storybook** | https://tomplum.github.io/react-git-log/ |

### Pourquoi cette librairie

- Rendu visuel très abouti et personnalisable (le plus proche de nos attentes en terme de rendu)
- Architecture bien découplée : algorithmes de layout en TypeScript pur, séparés du rendu React
- ~50-60% du code source est du TypeScript pur réutilisable directement
- 3 points d'extension via render props (custom nodes, custom tooltips, custom table rows)
- Support du theming (dark/light), palettes de couleurs multiples
- Pagination client-side et server-side
- Filtrage avec reroutage automatique des edges
- Pseudo-commit "index" pour le working directory
- Licence Apache-2.0 permissive
- Code source bien structuré, testé, et documenté

---

## 2. Présentation de @tomplum/react-git-log

### Structure du projet source

Le projet est un **monorepo** npm workspaces avec deux packages :

| Package | Nom npm | Rôle |
|---------|---------|------|
| `packages/library` | `@tomplum/react-git-log` | Bibliothèque React publiée sur npm |
| `packages/demo` | `@tomplum/react-git-log-demo` | Application Storybook de démo |

### Dépendances runtime

| Package | Version | Rôle |
|---------|---------|------|
| `dayjs` | `^1.11.13` | Manipulation et formatage de dates (plugins : `utc`, `advancedFormat`, `relativeTime`) |
| `fastpriorityqueue` | `^0.7.5` | File de priorité rapide (tri topologique) |
| `classnames` | `^2.5.1` | Utilitaire pour les classes CSS conditionnelles |
| `react-tiny-popover` | `^8.1.6` | Tooltips/popovers pour les nœuds de commit |
| `@uidotdev/usehooks` | `^2.4.1` | Collection de hooks React utilitaires |
| `react` | `>=19.0.0` | Framework UI |
| `react-dom` | `>=19.0.0` | Rendu DOM React |

### Stack technique

- **Build** : Vite 7, TypeScript 5.9, ESM uniquement
- **Tests** : Vitest 4 + Testing Library
- **CSS** : SCSS Modules (32 fichiers `.module.scss`) + inline styles dynamiques
- **Contrainte moteur** : Node >= 22.0.0

> **Note** : Le `package.json` de la librairie n'a pas été inclus dans le dossier `react-git-log-source/` copié. Les versions exactes des dépendances et les contraintes moteur listées ci-dessus sont celles documentées mais ne peuvent pas être vérifiées directement. Pour de futures revues, il serait utile d'inclure le `package.json` dans le dossier source.
