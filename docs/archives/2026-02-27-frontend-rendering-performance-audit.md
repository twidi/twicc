# 🔍 Rapport d'audit de performance de rendu — Frontend TwiCC

## Table des matières
1. [Problèmes critiques](#1-critiques)
2. [Problèmes importants](#2-importants)
3. [Problèmes modérés](#3-modérés)
4. [Problèmes mineurs](#4-mineurs)
5. [Bonnes pratiques constatées](#5-points-positifs)

---

## 1. Problèmes critiques (impact élevé)

### 1.1 ✅ ~~`SessionList.vue` — Timer 1s provoquant des re-rendus globaux~~

**Résolu** par l'extraction d'un composant `ProcessDuration` qui encapsule son propre `setInterval(1s)`. Le `ref(now)` n'est plus une dépendance réactive de `SessionList` ni de `SessionHeader` — seul le `<span>` du composant se re-rend chaque seconde, et uniquement pour les sessions en `assistant_turn`. Le composant supporte KeepAlive via `onActivated`/`onDeactivated`.

---

### 1.2 ✅ ~~`SessionList.vue` — Appels multiples redondants à `getProcessState()` et `getPendingRequest()` dans le template~~

**Résolu** par l'extraction d'un sous-composant `SessionListItem`. Chaque item fait ses lookups dans des `computed` (`processState`, `pendingRequest`, `project`) — un seul appel par getter par item, caché par Vue tant que les dépendances ne changent pas. `canStop` est aussi un computed au lieu d'une fonction appelée 3× dans le menu.

---

### 1.3 ⚪ ~~`getProjectSessions` / `getAllSessions` — Nouvelles listes triées créées à chaque accès~~ — **Négligeable**

**Fichier:** `data.js:169-184`

```js
getProjectSessions: (state) => (projectId) => {
    return Object.values(state.sessions)
        .filter(s => s.project_id === projectId && !s.parent_session_id)
        .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
        .sort(sessionSortComparator(state.processStates))
},
getAllSessions: (state) => {
    return Object.values(state.sessions)
        .filter(s => !s.parent_session_id)
        .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
        .sort(sessionSortComparator(state.processStates))
},
```

**Reclassé négligeable :** Après la résolution de 1.1 (timer 1s) et 1.2 (extraction de `SessionListItem`), ces getters ne sont plus réévalués chaque seconde. Le `computed` dans `SessionList` ne se réexécute que lorsqu'une dépendance réactive réelle change (ajout/modification de session, changement de `processStates`). Le coût O(n log n) sur quelques centaines de sessions est sous la milliseconde. Convertir en getters directs nécessiterait un getter par `projectId` ou un système de cache manuel, ajoutant de la complexité pour un gain imperceptible.

---

### 1.4 ✅ ~~`recomputeVisualItems` — Parsing JSON et reconstruction d'objets fréquents~~

**Résolu** par le lazy parsed content caching (`parsedContent.js`). Les `JSON.parse()` dans le backward walk passent par `getParsedContent()` (cache sur l'item). Le `JSON.stringify()` pour le workingMessage synthétique est remplacé par `setParsedContent()`.

---

### 1.5 ⚪ ~~`recomputeAllVisualItems` — Recompute sur TOUTES les sessions~~ — **Négligeable**

**Fichier:** `data.js:1063-1067`

```js
recomputeAllVisualItems() {
    for (const sessionId of Object.keys(this.sessionItems)) {
        this.recomputeVisualItems(sessionId)
    }
},
```

**Reclassé négligeable :** Appelé uniquement quand le display mode change — une action utilisateur explicite extrêmement rare (quelques fois par session utilisateur au maximum). Le coût ponctuel de N × `recomputeVisualItems` est acceptable pour un événement aussi peu fréquent. Ajouter un système d'invalidation lazy (flag + recompute à l'affichage) introduirait de la complexité pour un gain imperceptible.

---

## 2. Problèmes importants

### 2.1 ⚪ `computeVisualItems` (`visualItems.js`) — Multi-passes coûteux — **Négligeable**

**Fichier:** `utils/visualItems.js`

En mode conversation, `computeVisualItems` fait :
1. **Passe 1 :** Parcourt tous les items pour trouver `keptAssistant` messages
2. **Passe 2 :** Construit les `blockIds` (Sets et Maps créés)
3. **Passe 3 :** Construit le résultat final

Chaque appel crée de nouvelles structures (`new Set()`, `new Map()`, nouveaux objets pour chaque visual item). C'est un coût O(n) qui s'additionne avec les appels fréquents à `recomputeVisualItems`.

**Reclassé négligeable :** Le coût JS pur (3 × O(n) sur des primitives et Map/Set) est sous la milliseconde pour des milliers d'items. Depuis la stabilisation des références de visual items, ce calcul ne provoque plus de re-renders Vue inutiles — seuls les items réellement modifiés sont re-rendus. Optimiser davantage (append incrémental, splice pour les groupes) ajouterait beaucoup de complexité pour un gain à peine mesurable.

---

### 2.2 ⚪ `positions` computed dans `useVirtualScroll.js` — Recalcul total à chaque changement de hauteur — **Négligeable**

**Fichier:** `useVirtualScroll.js:129-138`

```js
const positions = computed(() => {
    let top = 0
    return items.value.map((item, index) => {
        const key = itemKey(item)
        const height = heightCache.get(key) ?? minItemHeight
        const pos = { index, key, top, height }
        top += height
        return pos
    })
})
```

**Problème:** `heightCache` est un `reactive(new Map())`. À chaque `.set()` sur cette Map (c'est-à-dire à chaque fois qu'un item est mesuré par le ResizeObserver), Vue invalide le computed `positions`, qui recalcule **toutes** les positions cumulatives de tous les items.

**Impact:** Quand des items sont rendus pour la première fois, le ResizeObserver mesure plusieurs items en rafale. Chaque mesure déclenche un recalcul complet de `positions`. Avec un `batchUpdateItemHeights`, le problème est atténué, mais chaque batch cause quand même un recalcul O(n) complet.

**Reclassé négligeable :** O(n) itérations de `Map.get()` + somme cumulative + création d'objets simples `{ index, key, top, height }` — sous la milliseconde pour des milliers d'items. `batchUpdateItemHeights` regroupe déjà les mesures par callback ResizeObserver. Ce calcul ne provoque pas de re-renders Vue inutiles (il alimente un binary search et le calcul des spacers). Le commentaire dans le code mentionnant des « memoization strategies » peut être retiré.

---

### 2.3 ✅ ~~`SessionItem.vue` — `JSON.parse` dans un computed à chaque render~~

**Résolu** par le lazy parsed content caching. `SessionItem.vue` reçoit maintenant une prop `content` de type Object (déjà parsé via `getParsedContent()`). Plus aucun `JSON.parse` dans le composant.

---

### 2.4 ⚪ ~~`getProjects` getter — Tri O(n log n) à chaque accès~~ — **Négligeable**

**Fichier:** `data.js:195`

```js
getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
```

**Reclassé négligeable :** Ce getter Pinia est mémorisé automatiquement. Il n'est invalidé que quand `mtime` change réellement (Vue traque les propriétés accédées dans le comparateur de sort). `$patch` avec deep merge ne déclenche pas de réactivité si les valeurs sont identiques. Le sort O(n log n) sur < 50 projets est sous la microseconde. Les 7 consommateurs downstream (filtres, reduce) sont également triviaux et le diffing Vue empêche tout re-rendu DOM superflu.

---

## 3. Problèmes modérés

### 3.1 ⚪ ~~Settings store — Watcher `{ deep: true }` sur objet reconstruit~~ — **Négligeable**

**Fichier:** `settings.js:496-529`

**Reclassé négligeable :** Le `{ deep: true }` est en fait **nécessaire** avec ce pattern. Le getter retourne un nouvel objet à chaque évaluation (nouvelle référence) — sans `deep: true`, le callback se déclencherait à chaque évaluation du getter (car `oldRef !== newRef`). Le `deep: true` force Vue à comparer propriété par propriété, et comme les 24 propriétés sont toutes des primitives, le callback ne se déclenche que sur un changement réel. L'overhead du deep compare sur 24 primitives (24 comparaisons `===`) est négligeable. De plus, les settings ne changent que sur action utilisateur explicite (UI) ou sync WebSocket au connect — extrêmement rare.

---

### 3.2 ✅ ~~`VirtualScroller.vue` — `renderedItems` computed crée de nouveaux objets à chaque changement de range~~

**Résolu** par la stabilisation des références de visual items (`recomputeVisualItems`). Les wrappers `{ item, index, key }` sont toujours recréés, mais l'`item` qu'ils contiennent est une référence stabilisée — Vue constate que les props du `SessionItem` n'ont pas changé et skip le re-rendu.

---

### 3.3 ⚪ ~~`ContentList.vue` — `expandedInternalGroups` crée un `new Set()` à chaque accès~~ — **Négligeable**

**Fichier:** `ContentList.vue`

**Reclassé négligeable :** Le computed n'est invalidé que sur clic utilisateur (toggle de groupe interne) ou migration rare d'items — jamais par le scroll, les nouveaux items, ou le display mode. Le Set contient 0 ou 1 élément en pratique (la majorité des messages ont 0 internal groups). `new Set([])` est instantané. Le consommateur unique (`visibleItems`) doit de toute façon recalculer quand les groupes changent. Aucune interaction avec le virtual scroller ou la pipeline de visual items.

---

### 3.4 ⚪ `SessionItemsList.vue` — Inline arrow function dans le template — **Négligeable**

**Fichier:** `SessionItemsList.vue`

```html
:item-key="item => item.lineNum"
```

**Problème:** Cette arrow function inline crée une **nouvelle référence de fonction** à chaque re-rendu du composant parent. Comme `VirtualScroller` reçoit une nouvelle prop `itemKey`, cela pourrait invalider des computeds internes du virtual scroller qui dépendent de `itemKey`.

**Reclassé négligeable :** `useVirtualScroll` capture `itemKey` une seule fois à l'initialisation (pas via un ref). La nouvelle référence de fonction sur la prop est ignorée par le composable — impact zéro.

---

### 3.5 ⚪ `ToolUseContent.vue` — Timers de polling multiples — **Négligeable**

**Fichier:** `ToolUseContent.vue`

Ce composant utilise des `setInterval` pour :
1. Poller les résultats d'outils (tool_result)
2. Poller les liens d'agents (agent_link)

**Reclassé négligeable :** Le scénario « 20 timers simultanés » est irréaliste. Le polling ne se déclenche que pour des résultats pendants (conversation active), uniquement sur des `wa-details` ouverts (lazy rendering), et s'arrête dès que le résultat arrive. L'agent link polling est plafonné à 10 tentatives. Le composant gère correctement les pauses via KeepAlive. En navigation sur des sessions historiques, aucun polling n'est actif.

---

### 3.6 ✅ ~~`setProcessState` dans data store — Déclenche `recomputeVisualItems` à chaque changement d'état~~

**Résolu** par un guard `isAssistantTurn` dans `setProcessState`. Le recompute n'est déclenché que lorsque le booléen `isAssistantTurn` change réellement (entrée/sortie de `ASSISTANT_TURN`), pas sur chaque transition d'état ou mise à jour de `pending_request`.

---

## 4. Problèmes mineurs

### 4.1 ⚪ ~~`notifyProcessStateChange` instancie `useSettingsStore()` à chaque appel~~ — **Négligeable**

Anti-pattern Vue (appel de `useSettingsStore()` dans une fonction non-composable), mais Pinia le gère via singleton. Simple lookup, impact nul.

---

### 4.2 ⚪ ~~`SessionList.vue` — `getSessionDisplayName` est une fonction normale, pas un computed~~ — **Négligeable**

Opération triviale (accès à quelques propriétés). Depuis l'extraction dans `SessionListItem`, le re-rendu est scopé à l'item individuel.

---

### 4.3 ⚪ ~~`getProjectDisplayName` getter — Mutation dans un getter~~ — **Négligeable**

Anti-pattern Pinia (mutation de state dans un getter), mais le cache `localState.projectDisplayNames` est un objet non-réactif dans `localState` qui n'est pas traqué par les consommateurs du getter. Aucune boucle de réactivité observée en pratique.

---

### 4.4 ⚪ ~~`App.vue` — `toastTheme` computed crée un nouvel objet~~ — **Négligeable**

Le thème change extrêmement rarement (action utilisateur). Impact quasi nul.

---

## 5. Bonnes pratiques constatées ✅

Le code contient aussi plusieurs excellents patterns de performance qu'il faut souligner :

1. **Shared ResizeObserver** dans `VirtualScroller` — Un seul observer pour tous les items au lieu d'un par item. Excellent.

2. **Hysteresis dans le render range** — Buffers asymétriques load/unload pour éviter le thrashing. Très bien pensé.

3. **RAF-throttled scroll handler** — `requestAnimationFrame` pour lisser les événements de scroll.

4. **Anchor-based scroll preservation** — Sauvegarde d'ancrage par item+offset au lieu du scrollTop brut. Robuste.

5. **KeepAlive lifecycle management** — Suspend/resume du virtual scroller avec `VirtualScroller` : empêche la corruption du scrollTop.

6. **`$patch` avec merge profond** — Utilisation de `this.$patch({ projects: { [id]: project } })` pour ne déclencher les re-rendus que sur les propriétés modifiées.

7. **Batch height updates** — `batchUpdateItemHeights` dans le virtual scroller regroupe les mises à jour de hauteur en un seul update réactif.

8. **Debounced draft notifications** — 10s debounce pour les notifications de draft, évitant le spam WebSocket.

9. **Lazy imports** pour éviter les dépendances circulaires — Pattern cohérent et bien documenté.

---

## Résumé par priorité

| Priorité | Problème | Impact estimé |
|----------|----------|---------------|
| ✅ ~~Critique~~ | ~~Timer 1s dans SessionList~~ | ~~Re-rendu global chaque seconde~~ |
| ✅ ~~Critique~~ | ~~Appels multiples getProcessState/getPendingRequest par session item dans template~~ | ~~×10 par item × 30 visibles × 1/s~~ |
| ⚪ ~~Critique~~ | ~~getProjectSessions/getAllSessions non-cachés~~ | Négligeable (sous 1ms, déclenché uniquement sur changement réel) |
| ✅ ~~Critique~~ | ~~recomputeVisualItems avec JSON.parse/stringify fréquent~~ | ~~Parsing coûteux à chaque WS message~~ |
| ⚪ ~~Critique~~ | recomputeAllVisualItems sur toutes les sessions | Négligeable (display mode change extrêmement rare, action utilisateur explicite) |
| ⚪ ~~Important~~ | computeVisualItems multi-passes avec allocations | Négligeable (JS pur, sous 1ms) |
| ⚪ ~~Important~~ | positions computed recalculé intégralement | Négligeable (JS pur, sous 1ms) |
| ✅ ~~Important~~ | ~~SessionItem JSON.parse dans computed~~ | ~~Parsing de gros JSON par item~~ |
| ⚪ ~~Important~~ | getProjects tri à chaque invalidation | Négligeable (< 50 items, mémorisé, invalidé uniquement sur changement réel de mtime) |
| ⚪ ~~Modéré~~ | Settings watcher deep:true | Négligeable (deep:true nécessaire ici, 24 primitives, changements rares) |
| ✅ ~~Modéré~~ | ~~VirtualScroller renderedItems nouvelles refs~~ | ~~Possible re-rendu de slots~~ |
| ⚪ ~~Modéré~~ | ContentList new Set() dans computed | Négligeable (0-1 items, invalidé uniquement sur clic utilisateur) |
| ⚪ ~~Modéré~~ | Inline arrow function pour itemKey | Négligeable (capturé au mount) |
| ⚪ ~~Modéré~~ | ToolUseContent timers multiples | Négligeable (auto-limité, lazy) |
| ✅ ~~Modéré~~ | ~~setProcessState → recomputeVisualItems systématique~~ | ~~Recompute superflu fréquent~~ |

---

Le problème le plus impactant est probablement la **combinaison** du timer 1s dans `SessionList` + les appels redondants de getters non-cachés dans le template, car cela crée un "battement de cœur" de re-rendus qui se propage à travers toute la liste de sessions chaque seconde, même quand rien ne change visuellement.
