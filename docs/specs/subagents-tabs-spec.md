# Système d'Onglets pour Sessions et Subagents

## Contexte et Besoin

L'application affiche actuellement une session avec son header et sa liste d'items. On souhaite ajouter un système d'onglets permettant d'afficher plusieurs contenus liés à une session, notamment les subagents.

Un subagent est une session de type `SUBAGENT` avec un `parent_session` pointant vers la session principale. Les données d'un subagent sont structurellement identiques à celles d'une session (mêmes items, mêmes metadata). Le `session_id` d'un subagent est son `agent_id`.

### Objectifs

1. Ajouter un système d'onglets sous le header de la session principale
2. L'onglet principal "Session" affiche la liste des items de la session (non fermable)
3. Les onglets subagents affichent le header + la liste des items du subagent (fermables)
4. Les deux contenus doivent être "live" en parallèle (pas de démontage au changement d'onglet).
5. L'URL reflète toujours l'onglet actif
6. Le système d'onglets est générique (prévu pour d'autres contenus futurs : diff viewer, terminal, file explorer...)

### Limitations Actuelles

- **Ouverture uniquement via URL** : pas d'UI pour ouvrir un onglet subagent (prévu plus tard)
- **Un seul subagent via URL** : l'URL ne supporte qu'un subagent, mais l'architecture permet plusieurs onglets ouverts
- **Pas de WebSocket pour les subagents** : le watcher backend ne broadcast pas les updates de subagents. Le chargement initial fonctionne via API, les updates temps réel seront ajoutées plus tard
- **Pas de persistance localStorage** : les onglets ouverts sont mémorisés en mémoire uniquement (perdus au refresh)

---

## Architecture des Composants

### Hiérarchie Actuelle

```
SessionView.vue (~700 lignes)
├── Header (titre, stats, sélecteur de mode)
└── Virtual scroller + logique lazy loading
```

### Nouvelle Hiérarchie

```
SessionView.vue (simplifié)
├── SessionHeader (session principale, toujours visible)
│   └── titre, stats, date, sélecteur de mode
│
└── wa-tab-group
    ├── wa-tab-panel "main" (Session)
    │   └── SessionItemsList
    │
    └── wa-tab-panel "agent-xxx" (Subagent)
        └── SessionContent
            ├── SessionHeader (mode="subagent")
            └── SessionItemsList
```

### Composants à Créer

| Composant              | Rôle                                                                                                                                                        |
|------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `SessionContent.vue`   | Assemble SessionHeader + SessionItemsList. Conçu pour être générique (session ou subagent), mais utilisé uniquement pour les subagents dans l'implémentation actuelle. Reçoit `sessionId` + `parentSessionId` (optionnel) |
| `SessionHeader.vue`    | Affiche les stats de session (titre, message_count, total_cost, context_usage, date). Prop `mode` ("session" \| "subagent") pour les variantes             |
| `SessionItemsList.vue` | Virtual scroller, lazy loading, réconciliation, gestion des groupes. Contient la majorité du code actuel de SessionView                                     |

### Différences entre Header Session et Header Subagent

- **Header session** : inclut le sélecteur de mode (debug/normal/simplified), titre de session
- **Header subagent** : pas de sélecteur de mode (il est global), titre affiché sous forme "Agent {agent_id}" au lieu du titre de session

Le composant `SessionHeader` utilise une prop `mode` pour gérer ces variantes.

---

## Structure Visuelle

```
┌─────────────────────────────────────────────────────┐
│ SessionHeader (session principale)  [mode selector] │
│ [titre] [stats]                                     │
├─────────────────────────────────────────────────────┤
│ [Session]  [Agent a6c ×]  [Agent b7d ×]             │ ← onglets
├─────────────────────────────────────────────────────┤
│                                                     │
│  (si onglet Session actif)                          │
│    └── SessionItemsList                             │
│                                                     │
│  (si onglet Subagent actif)                         │
│    └── SessionContent                               │
│        ├── SessionHeader (mode="subagent")          │
│        └── SessionItemsList                         │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Système d'Onglets Web Awesome

### Composants Utilisés

- `wa-tab-group` : conteneur avec prop `active` pour l'onglet actif
- `wa-tab` : onglet dans le slot "nav"
- `wa-tab-panel` : contenu de l'onglet

### Onglets Fermables (Closable Tabs)

Web Awesome ne fournit pas de prop `closable` native. Il faut implémenter le bouton de fermeture manuellement en suivant la documentation (section "Closable Tabs"). Cela consiste à ajouter un `wa-icon-button` dans le contenu du `wa-tab` et gérer l'événement click pour fermer l'onglet.

### Identifiants et Labels

| Onglet             | ID (panel/name)                             | Label affiché                       |
|--------------------|---------------------------------------------|-------------------------------------|
| Session principale | `main`                                      | "Session"                           |
| Subagent           | `agent-{agentId}` (ex: `agent-a6c7d21e8f9`) | "Agent {shortId}" (ex: "Agent a6c") |

- L'ID utilise l'`agentId` complet pour garantir l'unicité
- Le label affiché utilise les 3 premiers caractères pour la lisibilité

### Événements

- `@wa-tab-show` : déclenché au changement d'onglet → met à jour l'URL via `router.push`
- Click sur le bouton close custom → retire l'onglet, navigue vers l'onglet de gauche

### Comportement de Fermeture

Quand on ferme un onglet :
1. L'onglet est retiré du tableau `openSubagentTabs`
2. Vue unmount automatiquement le `SessionContent` et ses enfants (cleanup des watchers, appels API, etc.)
3. Navigation vers l'onglet de gauche (si on ferme le dernier subagent, on revient sur "Session")

---

## Routes

### Structure des Routes Vue

```javascript
{
    path: '/project/:projectId',
    component: ProjectView,
    children: [
        { path: '', name: 'project' },
        {
            path: 'session/:sessionId',
            name: 'session',
            component: SessionView,
            children: [
                {
                    path: 'subagent/:subagentId',
                    name: 'session-subagent'
                }
            ]
        }
    ]
}
```

### Comportement des URLs

| URL                               | Onglet actif   | Onglets ouverts      |
|-----------------------------------|----------------|----------------------|
| `/project/X/session/Y`            | Session (main) | Session seule        |
| `/project/X/session/Y/subagent/Z` | Subagent Z     | Session + Subagent Z |

### Principe : URL = Source de Vérité

L'URL reflète toujours l'onglet actif :
- Clic sur un onglet → `router.push` vers l'URL correspondante
- Fermer un onglet → `router.push` vers l'onglet de gauche
- Arrivée directe sur URL avec subagent → ouvre les deux onglets, active le subagent

---

## Mémorisation des Onglets

### Stockage dans le Store Pinia

Ajout dans `localState` (mémoire uniquement, pas localStorage) :

```javascript
localState: {
    // ... existant ...
    sessionOpenTabs: {
        "session-abc": {
            tabs: ["main", "agent-a6c7d21"],
            activeTab: "agent-a6c7d21"
        }
    }
}
```

### Comportement de Navigation entre Sessions

| Action                     | Effet                                                  |
|----------------------------|--------------------------------------------------------|
| Changer de session (A → B) | `sessionOpenTabs["A"]` conservé en mémoire             |
| Revenir sur session A      | Restaure les onglets depuis `sessionOpenTabs["A"]`     |
| Revenir sur session A      | `router.replace` vers l'URL de l'onglet actif mémorisé |

Note : `router.replace` (pas `push`) pour éviter de polluer l'historique avec une entrée intermédiaire.

---

## API Backend

### Principe

Les mêmes vues backend sont réutilisées. L'astuce : pour les routes subagent, le paramètre s'appelle toujours `sessionId` (pas `subagentId`), ce qui permet de réutiliser les vues existantes sans modification. Le `parentSessionId` permet de distinguer une requête subagent d'une requête session normale.

### Nouvelles Routes API

```
# Session normale (existant)
GET /api/projects/:projectId/sessions/:sessionId/
GET /api/projects/:projectId/sessions/:sessionId/items/metadata/
GET /api/projects/:projectId/sessions/:sessionId/items/?range=...

# Subagent (nouveau, mêmes vues, paramètre sessionId = agentId)
GET /api/projects/:projectId/sessions/:parentSessionId/subagent/:sessionId/
GET /api/projects/:projectId/sessions/:parentSessionId/subagent/:sessionId/items/metadata/
GET /api/projects/:projectId/sessions/:parentSessionId/subagent/:sessionId/items/?range=...
```

### Validation Backend

Les vues doivent vérifier que le `sessionId` (qui est l'agent_id) a bien `parent_session_id == parentSessionId`. Si ce n'est pas le cas, retourner une erreur 404.

### Construction des URLs côté Frontend

Dans `SessionItemsList`, la construction d'URL dépend de la présence de `parentSessionId` :

```javascript
const baseUrl = parentSessionId
  ? `/api/projects/${projectId}/sessions/${parentSessionId}/subagent/${sessionId}`
  : `/api/projects/${projectId}/sessions/${sessionId}`
```

---

## Props des Composants

### SessionView

Pas de changement aux props (reçoit `projectId` et `sessionId` via route).

### SessionContent

```typescript
interface Props {
  sessionId: string        // ID de la session ou du subagent
  parentSessionId?: string // Si présent, c'est un subagent
}
```

### SessionHeader

```typescript
interface Props {
  sessionId: string
  mode: 'session' | 'subagent'
}
```

### SessionItemsList

```typescript
interface Props {
  sessionId: string
  parentSessionId?: string // Pour construire les URLs API
  projectId: string
}
```

---

## Tasks

### Task 1 : Refactorer SessionView.vue en composants séparés

Extraire `SessionView.vue` en trois composants distincts tout en gardant l'application fonctionnelle (sans onglets pour l'instant) :

- **SessionHeader.vue** : Extraire le header (titre, stats message_count/total_cost/context_usage, date). Prop `mode` ("session" | "subagent") pour les variantes. Le sélecteur de mode reste dans `SessionView`.
- **SessionItemsList.vue** : Extraire le virtual scroller et toute la logique associée (DynamicScroller, lazy loading, réconciliation, gestion des groupes, watchers). Reçoit `sessionId`, `parentSessionId` (optionnel), `projectId`.
- **SessionContent.vue** : Créer le wrapper qui assemble `SessionHeader` + `SessionItemsList`. Reçoit `sessionId` et `parentSessionId`.

À la fin de cette task, `SessionView` utilise `SessionHeader` (pour le header principal) et `SessionItemsList` (pour la liste). L'application fonctionne exactement comme avant, sans onglets.

### Task 2 : Ajouter le système d'onglets Web Awesome

Modifier `SessionView.vue` pour intégrer les onglets :

- Importer les composants Web Awesome (`wa-tab-group`, `wa-tab`, `wa-tab-panel`) dans `main.js`
- Ajouter le `wa-tab-group` sous le header principal
- L'onglet "Session" (non fermable) contient `SessionItemsList` directement
- Les onglets subagent (fermables) contiennent `SessionContent`
- Implémenter le bouton close custom selon la doc Web Awesome (section "Closable Tabs")

### Task 3 : Gérer les routes pour les subagents

- Ajouter la route enfant `subagent/:subagentId` dans le router Vue
- Dans `SessionView`, détecter la présence de `subagentId` dans la route
- Ouvrir l'onglet correspondant et le rendre actif
- Gérer la synchronisation URL ↔ onglet actif (`@wa-tab-show` → `router.push`)
- Gérer la fermeture d'onglet → navigation vers l'onglet de gauche

### Task 4 : Mémoriser les onglets ouverts dans le store

- Ajouter `sessionOpenTabs` dans `localState` du store Pinia
- Sauvegarder les onglets ouverts et l'onglet actif pour chaque session
- Au retour sur une session, restaurer les onglets depuis le store
- Faire `router.replace` vers l'URL de l'onglet actif mémorisé

### Task 5 : Ajouter les routes API backend pour les subagents

- Créer les nouvelles routes API : `/api/projects/:projectId/sessions/:parentSessionId/subagent/:sessionId/...`
- Réutiliser les vues existantes (le paramètre `sessionId` = agent_id fonctionne directement)
- Ajouter la validation : vérifier que `session.parent_session_id == parentSessionId`, sinon 404
