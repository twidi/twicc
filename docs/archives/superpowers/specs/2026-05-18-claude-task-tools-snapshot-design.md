# Claude Task Tools Snapshot — design

**Date :** 2026-05-18
**Statut :** Draft (révisé)
**Scope :** Backend + Frontend (un composant)
**Worktree :** `feature/claude-task-tools-snapshot` (déjà créé dans `.worktrees/feature-claude-task-tools-snapshot`)

Document de cadrage pour enrichir le rendu UI des tools `TaskCreate`,
`TaskUpdate` et `TaskGet` de Claude Code en affichant, sous le JSON Human View
de l'input, la liste complète des tasks telle qu'elle existait à l'instant du
tool_use. Et pour garantir que ce snapshot, une fois capturé, ne soit jamais
réécrit lors d'un recompute.

**Note historique :** une première version de ce spec décrivait une lecture
disque (`~/.claude/tasks/<session>/*.json`) au moment du tool_use. Cette
approche est intrinsèquement défectueuse car le watcher TwiCC est toujours en
retard sur la CLI Claude Code — sur 3 `TaskCreate` consécutifs, les 3 fichiers
sont déjà tous écrits au moment où on traite la 1ère ligne tool_use. La lecture
disque renvoie donc un état "trop frais", incohérent avec l'instant du
tool_use. Ce spec décrit l'approche corrigée : **reconstruire l'état des tasks
à partir des inputs des tool_use eux-mêmes**, sans jamais lire les fichiers
disque.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- Pour les 3 tools by-id (`TaskCreate`, `TaskUpdate`, `TaskGet`), afficher dans
  le panneau de détail :
  1. Le JSON Human View de l'input (comportement actuel, inchangé).
  2. Un `<wa-divider>` séparateur.
  3. La liste complète des tasks de la session à l'instant du tool_use, rendue
     avec `TodoContent` — exactement comme le tool `TaskList` la rend
     aujourd'hui.
- Maintenir côté backend un **état interne** par session (`dict[task_id, task]`)
  reconstruit à partir des inputs des tool_use TaskCreate / TaskUpdate. Cet
  état est mis à jour live au fil du `transform_inline` et sert à enrichir
  chaque tool_use task avec `twiccTaskData` / `twiccTasksData` /
  `twiccTasksTotal`.
- Garantir qu'un block déjà enrichi (`twiccTaskData` présent pour les by-id,
  `twiccTasksData` présent pour `TaskList`) ne soit **jamais** réécrit lors
  d'un recompute. C'est ce qui préserve l'état historique.

### 0.2 Ce qu'on NE FAIT PAS

- **Pas de lecture des fichiers disque.** Toute l'information vient des inputs
  des tool_use dans le JSONL. Les fichiers `~/.claude/tasks/<session>/*.json`
  sont ignorés.
- **Pas de modification de l'enrichissement au moment du tool_result.** On
  reste sur l'enrichissement du tool_use block, évitant la complexité d'un
  système back/front pour notifier qu'un ancien `SessionItem` a été modifié.
- **Pas de bump de `CLAUDE_CODE_COMPUTE_VERSION`.** Les vieilles sessions
  garderont leur enrichissement disque-based (possiblement faux à cause de la
  race évoquée plus haut). Acceptable car ce projet est encore en
  développement et l'impact se limite aux sessions de test.
- **Pas de nouvelle table, pas de migration DB.** Tout est embarqué dans le
  JSONL enrichi stocké dans `SessionItem.content`.
- **Pas de modification de `TaskList`.** Son rendu reste celui d'aujourd'hui
  (TodoContent direct, pas de JsonHumanView au-dessus). Il bénéficie
  simplement de la nouvelle source de données (état interne au lieu du
  disque).
- **Pas de modification du contrat `BaseToolHelpers`** ni du shell
  `ToolUseContent.vue`. On s'inscrit dans le mécanisme `getInputRendering`
  existant.
- **Pas de changement du comportement de `hidesResult`** — les 4 task tools
  continuent de cacher leur tool_result.
- **Pas de titre/label** au-dessus de la liste snapshot ; le divider seul
  sépare.
- **Pas de tests automatisés** (conformément aux conventions du projet).
- **Pas de gestion des hooks côté agent.** Le code agent (qui pourrait poser
  des `PostToolUse` hooks pour pousser les snapshots) est hors scope. Cette
  approche est purement côté ingestion JSONL et fonctionne sans modification
  de l'agent.

### 0.3 Vocabulaire

| Terme | Définition |
|-------|-----------|
| **By-id task tools** | `TaskCreate`, `TaskUpdate`, `TaskGet` — les 3 tools qui agissent sur une task identifiable par id (créée, mise à jour, ou récupérée). |
| **État interne (task state)** | Structure mémoire `dict[session_id, dict[task_id_str, task_dict]]` maintenue par `ClaudeCodeSessionCompute`. Reconstruit à partir des inputs des tool_use. |
| **Snapshot** | La liste figée des tasks à un instant donné, embarquée dans le tool_use block sous la clé `twiccTasksData`. C'est la projection de l'état interne au moment du `transform_inline`. |
| **Enrichissement** | L'opération qui consiste à ajouter les clés `twiccXxx` à un tool_use block. Faite par `_enrich_task_tool_uses` dans `transform_inline`. |
| **Idempotence** | Garantie que l'enrichissement n'est jamais réécrit une fois posé. Repose sur les checks `'twiccTaskData' in block` (by-id) et `'twiccTasksData' in block` (TaskList) déjà présents. |
| **Restoration** | Lorsqu'on rencontre un block déjà enrichi, on en profite pour mettre à jour notre état interne (équivalent à un "restore from snapshot"). C'est nécessaire pour que les tool_use suivants soient enrichis avec un état cohérent. |
| **Reconstruction** | Cas où l'état interne est vide pour une session existante en DB. On cherche le dernier item de la session qui contient déjà `twiccTasksData`, on en restore l'état, puis on rejoue les tool_use task entre cet item et la ligne courante. |

---

## 1. Backend

### 1.1 Approche

Ne pas lire les fichiers disque. Reconstruire l'état des tasks à partir des
inputs des tool_use eux-mêmes, dans l'ordre où ils apparaissent dans le JSONL.

Les inputs portent toute l'information nécessaire :
- `TaskCreate` → `subject`, `description`, `activeForm`, `addBlocks`,
  `addBlockedBy`, `owner`, `metadata` (tous optionnels sauf subject) — pas
  d'`id` (attribué par notre code séquentiellement) ;
- `TaskUpdate` → `taskId` (requis) + tous les champs modifiables (status,
  subject, activeForm, description, addBlocks, addBlockedBy, owner,
  metadata) ;
- `TaskGet` → `taskId` seul ;
- `TaskList` → input vide.

### 1.2 État interne

Une structure mémoire portée par l'instance `ClaudeCodeSessionCompute` :

```python
_session_task_states: dict[str, dict[str, dict]]
#                     ^^^^^^   ^^^^^^   ^^^^
#                  session_id  task_id  task_dict (insertion-ordered)
```

- Clé externe : `session_id`.
- Valeur : un dict ordonné (préservation d'insertion = ordre de création) des
  tasks de cette session, indexées par leur id (en str pour cohérence avec les
  `taskId` reçus dans les inputs des tool_use).

Chaque `task_dict` contient :
- `id` : l'id séquentiel attribué par notre code, en str. Premier id = `"1"`,
  puis `"2"`, `"3"`… Le suivant est `str(max(int(k) for k in state) + 1)`,
  ou `"1"` si l'état est vide.
- `status` : `"pending"` au moment de la création, modifié par TaskUpdate.
- Tous les champs de l'input du tool_use, mergés tels quels (subject,
  description, activeForm, addBlocks, addBlockedBy, owner, metadata, …).

Pourquoi merger tels quels (sans interpréter) : si plus tard on veut afficher
addBlocks / addBlockedBy / owner / metadata dans la UI, ils seront déjà là.
Aucune sémantique additive sur addBlocks (qu'on traite comme un simple champ,
remplacé à chaque update).

### 1.3 Algorithme par tool

Dans `_enrich_task_tool_uses`, pour chaque tool_use block d'un tool task :

| Tool | Effet sur l'état | Champs écrits dans le tool_use |
|---|---|---|
| `TaskCreate` | Ajoute une nouvelle task `{id: next_id, status: "pending", **input}` (sans `taskId` puisque pas dans l'input) | `twiccTaskData` = la nouvelle task ; `twiccTasksData` = liste complète après création |
| `TaskUpdate` | Sur la task d'id `input.taskId` : `task.update({k: v for k, v in input.items() if k != "taskId"})` | `twiccTaskData` = la task mise à jour ; `twiccTasksData` = liste complète après update ; `twiccTasksTotal` = len(state) |
| `TaskGet` | Aucun effet sur l'état | `twiccTaskData` = la task lue (ou None → skip si taskId inconnu) ; `twiccTasksData` = liste complète ; `twiccTasksTotal` = len(state) |
| `TaskList` | Aucun effet sur l'état | `twiccTasksData` = liste complète |

`twiccTasksData` est toujours `list(state.values())` (préserve l'ordre
d'insertion = ordre de création par id).

### 1.4 Règle d'immutabilité + restoration

Lorsque `_enrich_task_tool_uses` rencontre un block déjà enrichi, il **ne le
réécrit pas**, mais il met quand même à jour notre état interne à partir du
snapshot pour que les tool_use suivants soient cohérents.

Algorithme :
1. Si `'twiccTasksData' in block` (cas TaskList ou by-id post-enrichissement) :
   - Lire `block['twiccTasksData']` comme la liste complète des tasks.
   - Reset l'état interne pour cette session à cette liste (dict reconstruit
     par task.id).
   - Skip l'écriture (immutabilité).
2. Sinon, si c'est un tool task non encore enrichi :
   - Appliquer l'algo §1.3 (advance state + enrich).

### 1.5 Reconstruction d'état

Scénario : `_enrich_task_tool_uses` est appelé pour une session dont notre état
interne est vide (état mémoire neuf, session existante en DB). On doit
reconstruire l'état correct avant de traiter le tool_use courant.

Algorithme `_rebuild_state_if_missing(session_id, current_line_num)` :
1. Si `session_id` est déjà dans `_session_task_states` → rien à faire.
2. Initialiser un état vide `_session_task_states[session_id] = {}`.
3. Chercher en DB le dernier `SessionItem` de la session, avec
   `line_num < current_line_num`, dont le `content` contient la chaîne
   `"twiccTasksData"` (Django `content__contains="twiccTasksData"`, déjà
   utilisé ailleurs dans le compute pour des lookups similaires).
4. Si trouvé :
   - Parser son content, extraire le `twiccTasksData` du premier tool_use
     block qui en porte.
   - Reset l'état interne à partir de cette liste (dict reconstruit par
     task.id).
   - Récupérer en DB tous les `SessionItem` entre cet item (exclus) et
     `current_line_num` (exclu), filtrer ceux qui contiennent
     `"name":"TaskCreate"` ou `"name":"TaskUpdate"` (pas besoin de
     TaskGet/List, ils ne modifient pas l'état).
   - Pour chaque tel item dans l'ordre : parser, appliquer la même logique
     §1.3 que pour une advance normale (TaskCreate → ajoute, TaskUpdate →
     merge). **Sans** réécrire l'item (on ne fait que rejouer son effet sur
     notre état).
5. Si non trouvé : l'état reste vide. Le prochain TaskCreate démarrera avec
   id=1.

Cette reconstruction est faite **une fois par session** au premier
`transform_inline` qui en a besoin. Après ça, l'état vit normalement en
mémoire pour toute la durée du process.

### 1.6 Cycle de vie de l'état interne

- **Live (watcher)** : l'état persiste dans l'instance singleton pour toute la
  durée du process. Reconstruction lazy à la première touche d'une session
  inconnue.
- **Batch (compute background)** : l'instance est éphémère (process séparé).
  On peut soit reconstruire lazy comme en live, soit pré-initialiser via
  `begin_session_compute` (qui est appelé par la base avant chaque session
  traitée en batch). Pour rester simple et cohérent, **on garde la
  reconstruction lazy** (même algorithme dans les deux modes), et on ne
  surcharge pas `begin_session_compute` / `end_session_compute`.

### 1.7 Fichiers touchés (backend)

| Action | Fichier | Responsabilité |
|---|---|---|
| Modifier | `src/twicc/providers/claude_code/compute.py` | Refactor `_enrich_task_tool_uses` (méthode d'instance maintenant, plus une fonction libre, car elle a besoin d'accéder à `self._session_task_states`). Ajouter `__init__` à `ClaudeCodeSessionCompute` pour initialiser `_session_task_states = {}`. Ajouter helpers `_rebuild_state_if_missing`, `_apply_task_create`, `_apply_task_update`. Retirer l'import `TasksReader`. |
| Supprimer | `src/twicc/providers/claude_code/tasks.py` | La classe `TasksReader` devient inutile — plus aucune lecture disque. Tout le fichier disparaît. |

### 1.8 Pas de bump de CLAUDE_CODE_COMPUTE_VERSION

Comme convenu : les vieilles sessions (avec enrichissement disque-based
possiblement faux) ne sont pas recomputées. L'utilisateur retestera ses
propres sessions actives.

---

## 2. Frontend

### 2.1 Composant (déjà créé)

`frontend/src/components/session/detail/items/claude_code/TaskByIdContent.vue`
existe déjà sur la branche (commit `c86f99c7`). Pas de changement nécessaire :
il consomme `twiccTaskData` / `twiccTasksData` / `twiccTasksTotal` du tool_use
block sans se soucier de leur source.

### 2.2 Helper (déjà câblé)

`frontend/src/providers/claude_code/toolHelpers.js` a déjà la branche pour les
3 by-id tools dans `getInputRendering` (commit `78f784a3`). Pas de changement
nécessaire.

### 2.3 ContentList docstring (déjà mis à jour)

`frontend/src/components/session/detail/items/claude_code/ContentList.vue` a
déjà sa docstring de `getToolExtra` qui mentionne `twiccTasksData` sur les
by-id tools (commit `2b652f3f`). Pas de changement nécessaire.

### 2.4 TaskList — inchangé

Comme avant.

---

## 3. Cas limites

| Cas | Comportement |
|---|---|
| Block legacy disque-based (twiccTaskData + twiccTasksData déjà présents) | Immutabilité préservée : on lit les snapshots existants, on reset notre état interne en conséquence, on n'écrit rien. La liste affichée reste celle capturée à l'époque (possiblement fausse à cause de la race, mais c'est l'historique). |
| Block jamais enrichi (premier touch d'une session) | Reconstruction d'état (§1.5), puis advance normal. |
| `TaskUpdate` avec un `taskId` inconnu de notre état | On skip l'update (pas d'enrichissement, ne perturbe pas l'état). Cas pathologique : l'agent fait un Update sur une task créée hors de notre vision. La reconstruction §1.5 devrait normalement éviter ça. |
| `TaskGet` avec un `taskId` inconnu | Pas d'enrichissement `twiccTaskData`, mais `twiccTasksData` et `twiccTasksTotal` sont quand même écrits (l'état liste est valide). |
| Input malformé (subject manquant pour TaskCreate, taskId manquant pour TaskUpdate/Get) | Skip silencieux pour ce block. Pas de log d'erreur (cas attendu rare, géré comme aujourd'hui). |
| Session sans aucun tool_use task → ouverture d'un TaskList tout seul | État vide → `twiccTasksData = []` → côté front, `tasksDataToTodos([])` renvoie null → section liste cachée. JsonHumanView du TaskList (input vide) reste seul. |
| Race entre 3 TaskCreate consécutifs | Aucune. Chaque TaskCreate est traité dans l'ordre du JSONL ; notre état reflète exactement l'état après ce TaskCreate (et avant le suivant). C'est précisément le bug que cette refactor corrige. |
| Replay/watcher qui retraite une ligne déjà enrichie | Restoration (§1.4) : on lit le snapshot, on reset l'état, on n'écrit rien. |

---

## 4. Vérification manuelle

Pas de tests automatisés (cf. CLAUDE.md). Plan de vérif manuel :

1. Lancer une **nouvelle** session Claude Code (DB worktree, pas la prod).
2. Faire 3 `TaskCreate` consécutifs (subjects distincts). Ouvrir chaque
   tool_use :
   - Le 1er montre 1 task (la sienne) en pending.
   - Le 2ème montre 2 tasks (les 2 premières) en pending.
   - Le 3ème montre 3 tasks (toutes) en pending.
   - **C'est le point clé** — c'est ce bug que la refactor corrige.
3. Faire un `TaskUpdate` pour passer la task #2 en `in_progress`. Ouvrir le
   tool_use → la liste affiche task #1 pending, task #2 in_progress, task #3
   pending.
4. Faire un nouveau `TaskUpdate` pour passer la task #2 en `completed`.
   Retourner sur le précédent TaskUpdate → la liste affiche **toujours**
   task #2 en `in_progress` (snapshot figé). Le nouveau tool_use affiche task
   #2 en `completed`.
5. Faire un `TaskGet` sur task #1 → la liste reflète l'état au moment du Get.
6. Faire un `TaskList` → comportement inchangé (liste seule, sans JSON Human
   View, sans divider).
7. **Régression** : ouvrir une session pré-existante (avant ce changement)
   avec des `TaskCreate`/`TaskUpdate` historiques. Les cards by-id doivent
   afficher leurs snapshots tels qu'enrichis à l'époque (les snapshots
   disque-based imparfaits restent). Pas de crash, pas de divider/liste
   manquant ou réécrit.

---

## 5. Rollback

- **Frontend** : retirer la branche `TaskCreate/TaskUpdate/TaskGet` ajoutée
  dans `getInputRendering` ramène au comportement d'avant. Composant et
  helper restent inertes.
- **Backend** : si l'état interne pose problème, on peut retirer la méthode
  `_enrich_task_tool_uses` entièrement. Les tool_use task ne seront plus
  enrichis (anciens et nouveaux) — la UI dégrade gracieusement (JSON Human
  View seul, pas de divider/liste). Le champ `twiccTasksData` reste sur les
  blocks legacy mais devient inerte.

Aucune migration, aucune compatibilité de wire à gérer.
