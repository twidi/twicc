# Smart Cache & Fetch Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ne fetcher les données que si elles ne sont pas déjà en cache, et ignorer les messages WebSocket pour des données non encore fetchées.

**Architecture:** Ajouter un objet `localState` séparé dans le store pour tracker l'état de fetch des projets et sessions. Cette séparation garantit que les données serveur peuvent être écrasées/mises à jour sans perdre l'état local.

**Tech Stack:** Vue 3, Pinia, JavaScript

---

## Task 1: Ajouter localState dans le store

**Files:**
- Modify: `frontend/src/stores/data.js:6-11`

**Step 1: Ajouter localState au state**

Modifier le state pour ajouter l'objet `localState` :

```javascript
state: () => ({
    // Server data
    projects: {},       // { id: { id, sessions_count, mtime, archived } }
    sessions: {},       // { id: { id, project_id, last_line, mtime, archived } }
    sessionItems: {},   // { sessionId: [{ line_num, content }, ...] }

    // Local UI state (separate from server data to avoid being overwritten)
    localState: {
        projects: {},   // { projectId: { sessionsFetched: boolean } }
        sessions: {}    // { sessionId: { itemsFetched: boolean } }
    },

    // Loading states
    loadingSessionItems: {} // { sessionId: boolean }
}),
```

**Step 2: Vérifier que l'app démarre sans erreur**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(store): add localState for cache tracking"
```

---

## Task 2: Ajouter les getters pour vérifier le cache

**Files:**
- Modify: `frontend/src/stores/data.js:13-24`

**Step 1: Ajouter les getters de vérification**

Ajouter après `isLoadingSessionItems` :

```javascript
hasSessionsForProject: (state) => (projectId) =>
    state.localState.projects[projectId]?.sessionsFetched ?? false,
hasItemsForSession: (state) => (sessionId) =>
    state.localState.sessions[sessionId]?.itemsFetched ?? false
```

Le bloc getters complet devient :

```javascript
getters: {
    // Sorted by mtime descending (most recent first)
    projectList: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
    getProject: (state) => (id) => state.projects[id],
    getProjectSessions: (state) => (projectId) =>
        Object.values(state.sessions)
            .filter(s => s.project_id === projectId)
            .sort((a, b) => b.mtime - a.mtime),  // sort by mtime descending
    getSession: (state) => (id) => state.sessions[id],
    getSessionItems: (state) => (sessionId) => state.sessionItems[sessionId] || [],
    isLoadingSessionItems: (state) => (sessionId) => state.loadingSessionItems[sessionId] || false,
    hasSessionsForProject: (state) => (projectId) =>
        state.localState.projects[projectId]?.sessionsFetched ?? false,
    hasItemsForSession: (state) => (sessionId) =>
        state.localState.sessions[sessionId]?.itemsFetched ?? false
},
```

**Step 2: Vérifier le build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(store): add getters to check cache status"
```

---

## Task 3: Modifier loadSessions pour utiliser localState

**Files:**
- Modify: `frontend/src/stores/data.js:70-84`

**Step 1: Mettre à jour loadSessions**

Remplacer la méthode `loadSessions` par :

```javascript
async loadSessions(projectId) {
    // Skip if already fetched
    if (this.localState.projects[projectId]?.sessionsFetched) {
        return
    }
    try {
        const res = await fetch(`/api/projects/${projectId}/sessions/`)
        if (!res.ok) {
            console.error('Failed to load sessions:', res.status, res.statusText)
            return
        }
        const sessions = await res.json()
        for (const s of sessions) {
            this.sessions[s.id] = s
        }
        // Mark as fetched in localState
        if (!this.localState.projects[projectId]) {
            this.localState.projects[projectId] = {}
        }
        this.localState.projects[projectId].sessionsFetched = true
    } catch (error) {
        console.error('Failed to load sessions:', error)
    }
},
```

**Step 2: Vérifier le build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(store): skip loadSessions if already cached"
```

---

## Task 4: Modifier loadSessionItems pour utiliser localState

**Files:**
- Modify: `frontend/src/stores/data.js:85-100`

**Step 1: Mettre à jour loadSessionItems**

Remplacer la méthode `loadSessionItems` par :

```javascript
async loadSessionItems(projectId, sessionId) {
    // Skip if already fetched
    if (this.localState.sessions[sessionId]?.itemsFetched) {
        return
    }
    this.loadingSessionItems[sessionId] = true
    try {
        const res = await fetch(`/api/projects/${projectId}/sessions/${sessionId}/items/`)
        if (!res.ok) {
            console.error('Failed to load session items:', res.status, res.statusText)
            return
        }
        const items = await res.json()
        this.sessionItems[sessionId] = items
        // Mark as fetched in localState
        if (!this.localState.sessions[sessionId]) {
            this.localState.sessions[sessionId] = {}
        }
        this.localState.sessions[sessionId].itemsFetched = true
    } catch (error) {
        console.error('Failed to load session items:', error)
    } finally {
        this.loadingSessionItems[sessionId] = false
    }
}
```

**Step 2: Vérifier le build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(store): skip loadSessionItems if already cached"
```

---

## Task 5: Modifier le handler WebSocket pour session_added

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js:38-40`

**Step 1: Conditionner l'ajout de session**

Modifier le case `session_added` pour ne l'ajouter que si les sessions du projet ont été fetchées :

```javascript
case 'session_added':
    // Only add if we've fetched sessions for this project
    if (store.hasSessionsForProject(msg.session.project_id)) {
        store.addSession(msg.session)
    }
    break
```

**Step 2: Vérifier le build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat(ws): ignore session_added for unfetched projects"
```

---

## Task 6: Modifier le handler WebSocket pour session_updated

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js:41-43`

**Step 1: Conditionner la mise à jour de session**

Modifier le case `session_updated` pour ne mettre à jour que si la session existe déjà :

```javascript
case 'session_updated':
    // Only update if session exists in store (was previously fetched)
    if (store.getSession(msg.session.id)) {
        store.updateSession(msg.session)
    }
    break
```

**Step 2: Vérifier le build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat(ws): ignore session_updated for unknown sessions"
```

---

## Task 7: Modifier le handler WebSocket pour session_items_added

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js:44-46`

**Step 1: Conditionner l'ajout d'items**

Modifier le case `session_items_added` pour ne les ajouter que si les items de la session ont été fetchés :

```javascript
case 'session_items_added':
    // Only add if we've fetched items for this session
    if (store.hasItemsForSession(msg.session_id)) {
        store.addSessionItems(msg.session_id, msg.items)
    }
    break
```

**Step 2: Vérifier le build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`
Expected: Build success

**Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat(ws): ignore session_items_added for unfetched sessions"
```

---

## Task 8: Test manuel de validation

**Step 1: Lancer l'application**

Run: `cd /home/twidi/dev/twicc-poc && uv run ./run.py`

**Step 2: Vérifier le comportement**

Ouvrir le browser et vérifier :

1. **Navigation projet** : Aller sur Projet A, puis Projet B, puis revenir sur Projet A
   - Expected: L'onglet Network ne montre PAS de nouvelle requête `/api/projects/A/sessions/` au retour

2. **Navigation session** : Aller sur Session 1, puis Session 2, puis revenir sur Session 1
   - Expected: L'onglet Network ne montre PAS de nouvelle requête `/api/.../sessions/1/items/` au retour

3. **WebSocket** : Vérifier que les mises à jour WebSocket fonctionnent toujours pour les données déjà chargées
   - Expected: Les nouvelles lignes apparaissent en temps réel sur une session ouverte

---

## Résumé des fichiers modifiés

| Fichier | Modifications |
|---------|---------------|
| `frontend/src/stores/data.js` | Ajout `localState`, getters, skip fetch si caché |
| `frontend/src/composables/useWebSocket.js` | Conditions sur les handlers WS |

## Structure finale du state

```javascript
state: () => ({
    // Server data
    projects: {},
    sessions: {},
    sessionItems: {},

    // Local UI state (never overwritten by server data)
    localState: {
        projects: {},   // { projectId: { sessionsFetched: true } }
        sessions: {}    // { sessionId: { itemsFetched: true } }
    },

    // Loading states
    loadingSessionItems: {}
})
```

## Notes importantes

- Les vues (`ProjectView.vue`, `SessionView.vue`) n'ont PAS besoin d'être modifiées car elles appellent toujours `loadSessions` / `loadSessionItems`, mais ces méthodes retournent immédiatement si déjà en cache.
- `localState` est séparé des données serveur pour éviter d'être écrasé lors des mises à jour.
- `loadingSessionItems` reste à sa place actuelle (migration vers `localState` possible plus tard si besoin).
