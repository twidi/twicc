# Virtual Scroller - Addendum 1: Exploration du code existant

Ce document contient l'analyse complète du système de virtual scrolling actuel réalisée par un agent d'exploration.

---

## RÉSUMÉ COMPLET - Système de Virtual Scrolling Vue.js/Django

### 1. VUE-VIRTUAL-SCROLLER - Utilisation actuelle

**Version:** `vue-virtual-scroller@^2.0.0-beta.8` (package.json:28)

**Composant principal:** `DynamicScroller`
- **Fichier:** `/home/twidi/dev/twicc-poc/frontend/src/components/SessionItemsList.vue` (556 lignes)
- **Configuration clé (lignes 427-437):**
  ```vue
  <DynamicScroller
      :key="sessionId"
      ref="scrollerRef"
      v-else-if="visualItems.length > 0"
      :items="visualItems"
      :min-item-size="MIN_ITEM_SIZE"    <!-- 24px (ligne 42) -->
      :buffer="500"                      <!-- Grands buffers pour pre-rendering -->
      key-field="lineNum"
      class="session-items"
      :emit-update="true"
      @update="onScrollerUpdate"
  >
  ```

**Hacks/Tweaks mis en place:**

1. **DOM Ordering Fix (ligne 330-334):** Appelle `recycleScroller.sortViews()` pour forcer l'ordre DOM à correspondre à l'ordre visuel
   ```javascript
   function sortScrollerViews() {
       const recycleScroller = scrollerRef.value?.$refs?.scroller
       if (recycleScroller?.sortViews) {
           recycleScroller.sortViews()
       }
   }
   ```
   - Throttlé à 150ms pour scroll events (ligne 337)
   - Appelé à chaque update du scroller (ligne 368)
   - **Raison:** CSS sibling selectors pour les groupes collapsibles

2. **Size Dependencies (ligne 443):** Track les dépendances pour recalcul de hauteur
   ```vue
   :size-dependencies="[item.isExpanded, item.prefixExpanded, item.suffixExpanded]"
   ```
   - Permet au scroller de détecter les changements de hauteur d'item

3. **Flow-root Display (ligne 521-522):** Conteneur des items utilise `display: flow-root`
   ```css
   display: flow-root; /* Block Formatting Context sans clip */
   ```
   - Évite le margin collapse à travers les items
   - Permet aux wa-card d'avoir des margins visibles à offsetHeight

4. **Scroll-to-bottom Retry (ligne 243-260):** Boucle avec délai pour compenser les dynamic heights
   ```javascript
   async function scrollToBottomUntilStable() {
       const maxAttempts = 10
       const delayBetweenAttempts = 50 // ms

       for (let attempt = 0; attempt < maxAttempts; attempt++) {
           scroller.scrollToBottom()
           await new Promise(resolve => setTimeout(resolve, delayBetweenAttempts))
           const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
           if (distanceFromBottom <= 5) break
       }
   }
   ```

---

### 2. SYSTÈME DE "VISUAL INDEX"

**Structure en deux niveaux:**

```
items (store.sessionItems)         visualItems (store.sessionVisualItems)
├─ Raw items avec metadata        ├─ Filtrés par display mode
├─ line_num (1-based)             ├─ groups collapsés/expandés
├─ content (JSONL)                └─ Prêts pour virtual scroller
├─ display_level (enum)
├─ group_head/group_tail
├─ kind
└─ Peut être null (placeholder)

Mapping: visualItems[0].lineNum → items[lineNum-1]
```

**Computation (visualItems.js:32-172):**
```javascript
export function computeVisualItems(items, mode, expandedGroups = []) {
    // Pour chaque item:
    // - DEBUG mode: affiche tout
    // - NORMAL mode: affiche levels 1,2 (cache level 3 DEBUG_ONLY)
    // - SIMPLIFIED: affiche level 1, collapse level 2 en groupes

    // Retourne array de visual items avec:
    // - lineNum: identifiant stable
    // - content: pour réactivité
    // - isGroupHead?, isExpanded?: pour COLLAPSIBLE qui démarrent des groupes
    // - prefixExpanded?, suffixExpanded?: pour ALWAYS avec groupes
}
```

**Constants (constants.js:39-43):**
```javascript
export const DISPLAY_LEVEL = {
    ALWAYS: 1,           // Toujours visible
    COLLAPSIBLE: 2,      // Groupable en simplified mode
    DEBUG_ONLY: 3,       // Caché en normal et simplified
}
```

**Identification des items:**
- Clé unique: `lineNum` (1-based, correspond à la ligne JSONL originale)
- Virtual scroller utilise `key-field="lineNum"` (SessionItemsList.vue:434)
- Les items peuvent avoir `content: null` (placeholders pour lazy loading)

**Range pour chargement:**
- Frontend accumule les `lineNum` des items sans content (lignes 378-384)
- Convert en ranges compacts via `lineNumsToRanges()` (lignes 266-286)
- Exemples: `[1, 2, 3, 5, 6] → [[1, 3], [5, 6]]`

---

### 3. STRUCTURE DES ITEMS DE SESSION

**En base de données (models.py:106-146):**
```python
class SessionItem(models.Model):
    id = BigAutoField                    # PK auto
    session = ForeignKey(Session)
    line_num = PositiveIntegerField      # 1-based, unique par session
    content = TextField                   # Raw JSONL line

    # Display metadata (computed)
    display_level = PositiveSmallIntegerField
    group_head = PositiveIntegerField    # line_num du groupe start
    group_tail = PositiveIntegerField    # line_num du groupe end
    kind = CharField                      # "user_message", "assistant_message", etc.

    # Cost/usage
    message_id = CharField
    cost = DecimalField
    context_usage = PositiveIntegerField

    class Meta:
        ordering = ["line_num"]
        constraints = [UniqueConstraint(session, line_num)]
        indexes = [Index(session, kind, line_num)]
```

**Structure en mémoire (store.js:10-51):**
```javascript
state: {
    // Server data
    projects: {},           // { id: {...} }
    sessions: {},           // { id: {...} }
    sessionItems: {},       // { sessionId: [{line_num, content, display_level, ...}, ...] }

    // Computed
    sessionVisualItems: {}  // { sessionId: [{lineNum, content, isExpanded?, ...}, ...] }
}
```

**Exemple item JSONL (complet):**
```json
{
  "type": "user_message|assistant_message|...",
  "message": {
    "role": "user|assistant",
    "content": [
      {"type": "text", "text": "..."},
      {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
      {"type": "tool_result", "tool_use_id": "...", "content": "..."}
    ]
  }
}
```

**Fetch strategy (SessionItemsList.vue:100-155):**
```javascript
async function loadSessionData(lastLine) {
    // 1. Load metadata (tous les items, SANS content)
    const metadata = await store.loadSessionMetadata(...)
    store.initSessionItemsFromMetadata(sessionId, metadata)

    // 2. Load initial content (first N + last N)
    if (lastLine <= INITIAL_ITEMS_COUNT * 2) {
        ranges = [[1, lastLine]]
    } else {
        ranges = [
            [1, INITIAL_ITEMS_COUNT],
            [lastLine - INITIAL_ITEMS_COUNT + 1, lastLine]
        ]
    }
    const items = await fetch(`/items/?range=1:100&range=900:1000`)
    store.updateSessionItemsContent(sessionId, items)
}
```

**INITIAL_ITEMS_COUNT = 100** (constants.js:11)

---

### 4. COMPOSANT SessionItemsList - La pièce maîtresse

**Fichier:** `/home/twidi/dev/twicc-poc/frontend/src/components/SessionItemsList.vue`

**Props:**
```javascript
defineProps({
    sessionId: String,           // Identifiant de session
    parentSessionId: String,     // null pour sessions, set pour subagents
    projectId: String
})
```

**Lifecycle:**

1. **Mount + Watch (lignes 158-188):**
   - Quand sessionId change → load metadata + initial content
   - Attend compute_version_up_to_date==true
   - Scroll to bottom après load

2. **Scroller Update Event (lignes 366-390):**
   - Émis par DynamicScroller quand viewport change
   - Donne indices visibles: `startIndex, endIndex, visibleStartIndex, visibleEndIndex`
   - **Important:** indices dans `visualItems`, PAS dans `items` bruts

3. **Lazy Loading Flow (lignes 373-389):**
   ```
   Scroll → onScrollerUpdate(startIndex, endIndex, visStart, visEnd)
       ↓
   Ajoute LOAD_BUFFER (50 items) autour de [visStart, visEnd]
       ↓
   Collecte lineNum des items sans content dans [bufferedStart, bufferedEnd]
       ↓
   Debounce 150ms + accumule ranges
       ↓
   Exécute fetch rangé + update UI + scroll-to-bottom si nécessaire
   ```

**Key Functions:**

| Fonction | Ligne | Rôle |
|----------|-------|------|
| `onScrollerUpdate()` | 366 | Handle scroller events, trigger lazy load |
| `executePendingLoad()` | 292 | Execute accumulated load request |
| `lineNumsToRanges()` | 266 | Compact line numbers into ranges |
| `scrollToBottomUntilStable()` | 243 | Retry scroll to stabilize layout |
| `sortScrollerViews()` | 329 | Force DOM order for CSS selectors |

**Events Émis:**
- `@toggle-suffix` (ligne 481) - Quand un group suffix est togglé

**Template:**
```vue
<DynamicScroller ... @update="onScrollerUpdate">
    <template #default="{ item, index, active }">
        <DynamicScrollerItem :size-dependencies="[isExpanded, prefixExpanded]">
            <!-- Group head avec toggle -->
            <template v-if="item.isGroupHead">
                <GroupToggle @toggle="toggleGroup(item.lineNum)" />
                <SessionItem v-if="item.isExpanded" :content="item.content" />
            </template>

            <!-- Item régulier (avec potential prefix/suffix toggles) -->
            <SessionItem v-else :content="item.content" />
        </DynamicScrollerItem>
    </template>
</DynamicScroller>
```

---

### 5. API BACKEND - Endpoints de chargement

**Django Views** (`views.py:77-148`):

| Endpoint | Méthode | Description | Params |
|----------|---------|-------------|--------|
| `/api/projects/` | GET | List projects | - |
| `/api/projects/{id}/sessions/` | GET | Sessions d'un projet | - |
| `/api/projects/{id}/sessions/{sid}/` | GET | Detail session | - |
| `/api/projects/{id}/sessions/{sid}/items/metadata/` | GET | ALL items sans content | - |
| `/api/projects/{id}/sessions/{sid}/items/` | GET | Items filtrés | `?range=1:100&range=900:1000` |

**Parsing des ranges (lignes 118-145):**
```python
ranges = request.GET.getlist("range")
# Format: "5" ou "10:20" ou "10:" ou ":20"

q_filter = Q()
for r in ranges:
    if ":" not in r:
        q_filter |= Q(line_num=line_val)
    else:
        min_val, max_val = parse_range(r)
        q_filter |= Q(line_num__gte=min_val, line_num__lte=max_val)

items = items.filter(q_filter)
```

**Serializers** (`serializers.py`):

```python
def serialize_session_item(item):  # Full
    return {
        "line_num": item.line_num,
        "content": item.content,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind
    }

def serialize_session_item_metadata(item):  # Sans content
    return {
        "line_num": item.line_num,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind
        # NO content!
    }
```

---

### 6. STORE (Pinia) - État central

**Fichier:** `/home/twidi/dev/twicc-poc/frontend/src/stores/data.js` (837 lignes)

**State tree:**
```javascript
{
    // Server data (synced depuis API + WebSocket)
    projects: { id: {...} },
    sessions: { id: {...} },
    sessionItems: { sessionId: [...items...] },

    localState: {
        // Loading states
        projects[projectId]: { sessionsFetched, sessionsLoading, sessionsLoadingError }
        sessions[sessionId]: { itemsFetched, itemsLoading, itemsLoadingError }

        // UI state
        sessionItemsDisplayMode: 'debug|normal|simplified',
        sessionExpandedGroups: { sessionId: [groupHeadLineNum, ...] },
        sessionInternalExpandedGroups: { sessionId: { lineNum: [startIndex, ...] } },
        sessionVisualItems: { sessionId: [...computed...] },
        sessionOpenTabs: { sessionId: { tabs: [...], activeTab: 'main' } },
        agentLinks: { sessionId: { toolId: agentId|null } }
    }
}
```

**Actions clé:**

| Action | Signature | Rôle |
|--------|-----------|------|
| `initSessionItems()` | (sessionId, lastLine) | Crée array de placeholders |
| `addSessionItems()` | (sessionId, newItems, updatedMetadata) | Merge items, recompute visual |
| `loadSessionMetadata()` | (projectId, sessionId, parentSessionId) | Fetch metadata |
| `loadSessionItemsRanges()` | (projectId, sessionId, ranges, parentSessionId) | Fetch content |
| `initSessionItemsFromMetadata()` | (sessionId, metadata) | Build array from metadata |
| `updateSessionItemsContent()` | (sessionId, items) | Merge content into array |
| `recomputeVisualItems()` | (sessionId) | Recalculate visual items |
| `toggleExpandedGroup()` | (sessionId, groupHeadLineNum) | Toggle collapse state |

**Getters:**
```javascript
getSessionItems(sessionId)       // items bruts
getSessionVisualItems(sessionId) // items filtrés
getExpandedGroups(sessionId)     // array de groupHeadLineNum
isGroupExpanded(sessionId, lineNum)
getSessionItem(sessionId, lineNum) // Single item by lineNum
```

---

### 7. FLOW COMPLET D'UN CHARGEMENT

```
1. User clicks on session
   ↓
2. SessionView renders SessionItemsList with sessionId
   ↓
3. Watch triggered → loadSessionData()
   ↓
4. Parallel:
   a) fetch /items/metadata/
      → store.initSessionItemsFromMetadata()
      → creates sessionItems array with metadata only
      → recomputeVisualItems()

   b) fetch /items/?range=1:100&range=N-100:N
      → store.updateSessionItemsContent()
      → fills content in array
      → recomputeVisualItems()
   ↓
5. scrollToBottomUntilStable() with retries
   ↓
6. User scrolls
   ↓
7. DynamicScroller emits @update(startIndex, endIndex, visStart, visEnd)
   ↓
8. onScrollerUpdate():
   - Collect lineNums without content in [visStart-50, visEnd+50]
   - Debounce 150ms
   ↓
9. executePendingLoad():
   - lineNumsToRanges([1,2,5,6,10]) → [[1,2],[5,6],[10,10]]
   - fetch /items/?range=1:2&range=5:6&range=10:10
   ↓
10. store.addSessionItems(sessionId, items)
    → merge into existing array
    → recomputeVisualItems()
    → DynamicScroller detects changes
    → Updates virtualItems, re-renders visible items
```

---

### 8. RÉSUMÉ PAR FICHIER

| Fichier | Lignes | Rôle |
|---------|--------|------|
| **SessionItemsList.vue** | 556 | Composant principal, virtual scroller, lazy loading orchestration |
| **data.js (Store)** | 837 | État central, actions CRUD, recompute visual items |
| **visualItems.js** | 172 | Compute visual items par display mode et expanded groups |
| **constants.js** | 44 | Display levels, modes, initial items count |
| **views.py** | 300+ | API endpoints, range parsing, serialization |
| **serializers.py** | 111 | JSON serialization (full + metadata) |
| **models.py** | 200+ | SessionItem, Session models, indexes |

---

### 9. KEY INSIGHTS

1. **Hybrid Loading:** Metadata séparé du content permet de montrer la structure dès le départ
2. **Line Numbers:** 1-based `lineNum` est l'identifiant stable, clé du mapping visualItems ↔ items
3. **Grouping:** Deux systèmes de groupes coexistent:
   - **External groups:** `group_head/group_tail` dans SessionItem (collapsibles en simplified)
   - **Internal groups:** À l'intérieur du JSON content des ALWAYS items
4. **Lazy Loading:** Basé sur visual index range + buffer, NOT sur scroll position
5. **DOM Order:** Hack `sortViews()` obligatoire pour CSS sibling selectors avec groupes
6. **Size Dependencies:** Permet au virtual scroller de recalculer quand items expand/collapse

---

Voilà ! C'est un système très sophistiqué qui combine:
- Virtual scrolling dynamique (hauteurs variables)
- Lazy loading intelligente (avec accumulation et ranges)
- Double système de groupes (extern et intern)
- Multi-mode display (debug/normal/simplified)
- Subagents imbriqués

Tous les fichiers clés sont localisés et les flow complets sont documentés !
