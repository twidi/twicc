# Codex approvals — analyse complète & plan d'implémentation

Document préparatoire à l'implémentation. Tu peux le lire de bout en bout : il décrit le protocole Codex, ce qui existe déjà côté Claude dans TwiCC, ce qu'on doit factoriser, ce qu'on doit ajouter, et termine par une **liste de questions** (§7) à trancher avant de coder.

Toutes les citations sont du type `path:line`. Worktree : `feature/multi-provider`.

---

## 0. Cadrage

### 0.1 Ce qu'on veut

- Lever le bypass actuel (`approval_policy="never"` + `sandbox=danger_full_access`) côté Codex.
- Afficher dans TwiCC les demandes d'approbation que Codex envoie en cours de turn (commandes shell, modifications de fichiers, requêtes de permissions).
- Permettre à l'utilisateur d'approuver/refuser/annuler depuis le frontend, avec routage synchrone vers le SDK.
- **Réutiliser au maximum** l'infrastructure `PendingRequest` déjà en place pour Claude (UI, état Pinia, snapshot agent, broadcast WS).

### 0.2 Ce qu'on NE FAIT PAS dans ce chantier

- **Pas de `ask_user_question`** côté Codex. Le user l'a explicitement dit : le système Claude « tool_approval + ask_user_question » devient « tool_approval seul » côté Codex. Donc on **ignore** :
  - `item/tool/requestUserInput` (experimental, géré par `request_user_input` tool — voir Rust `codex-rs/app-server-protocol/schema/json/ServerRequest.json:1800-1824`)
  - `mcpServer/elicitation/request` (formulaires interactifs MCP — `ServerRequest.json:1826-1849`)
  - `thread/approveGuardianDeniedAction` (override d'une décision guardian — c'est un **client** request, donc *nous* qui pourrions l'envoyer à Codex, pas l'inverse ; pas notre cas tant qu'on n'utilise pas le mode guardian)
- **Pas de mode runtime style Claude** (changement live default/acceptEdits/bypassPermissions/plan en cours de session). On utilise bien le champ `Session.permission_mode` côté DB, mais comme un **préset déterminé au démarrage** qui résout vers `(sandbox_mode, approval_policy)` au `thread_start`. Pas d'overide mid-session via WS (cf. §4 Étape 7).
- **Pas d'auto-approval / guardian** (`approvalsReviewer="auto_review"` ou `"guardian_subagent"`). On reste sur le mode humain (`approvalsReviewer="user"` par défaut). Les events `item/autoApprovalReview/started`/`completed` ne seront pas émis dans notre configuration.

### 0.3 Glossaire rapide

| Terme | Définition |
|------|-----------|
| **App-server protocol** | JSON-RPC sur stdio que parle le binaire `codex app-server`. C'est celui qu'on utilise via le SDK Python `codex_app_server` (vendored). |
| **Server request** | JSON-RPC envoyé par le serveur Codex au client (nous), **avec** un `id` ⇒ attend une réponse synchrone. |
| **Notification** | JSON-RPC envoyé par le serveur, **sans** `id` ⇒ pas de réponse attendue. |
| **`approval_policy`** | Quand Codex doit demander l'autorisation : `untrusted` / `on-failure` / `on-request` / `granular{…}` / `never`. |
| **`sandbox`** / **`SandboxMode`** | Comment Codex isole l'agent : `read-only` / `workspace-write` / `danger-full-access` (+ `external-sandbox` à part). |
| **`approvals_reviewer`** | Qui répond aux demandes : `user` (= nous, normal) / `auto_review` / `guardian_subagent`. On reste sur `user`. |

---

## 1. État de l'art Codex (Rust + SDK Python)

### 1.1 Les trois requêtes d'approval v2

Toutes sont des **JSON-RPC server requests** (avec `id`, réponse synchrone obligatoire sinon le turn reste bloqué). Source : `/home/twidi/dev/codex/codex-rs/app-server-protocol/schema/json/ServerRequest.json:1750-1874`.

| Méthode | Quand | Réponse |
|--------|-------|---------|
| `item/commandExecution/requestApproval` | Exécution de commande shell / sub-exec / accès réseau | `{decision: CommandExecutionApprovalDecision}` |
| `item/fileChange/requestApproval` | Application d'un patch (add/modify/delete fichier) | `{decision: FileChangeApprovalDecision}` |
| `item/permissions/requestApproval` | Le tool `request_permissions` du modèle demande plus de permissions filesystem/réseau | `{permissions, scope, strictAutoReview?}` |

#### 1.1.a `item/commandExecution/requestApproval`

**Params** (`ServerRequest.json:345-442`, vérifié) :

| Champ | Type | Obligatoire | Description |
|-------|------|-------------|-------------|
| `threadId` | `string` | ✅ | |
| `turnId` | `string` | ✅ | |
| `itemId` | `string` | ✅ | Corrèle avec un item `ExecCommandBegin` |
| `startedAtMs` | `int64` | ✅ | Timestamp ms |
| `approvalId` | `string\|null` | ❌ | Présent pour sub-exec (un même `itemId` peut générer plusieurs approvals) |
| `command` | `string\|null` | ❌ | La commande shell-joinée (null pour les approvals réseau pures) |
| `cwd` | `AbsolutePathBuf\|null` | ❌ | Working directory |
| `commandActions` | `CommandAction[]\|null` | ❌ | Parsing best-effort : `{type: "read"\|"listFiles"\|"search"\|"unknown", command, path?, query?, name?}` — utile pour rendre l'UI |
| `reason` | `string\|null` | ❌ | Justification du modèle |
| `networkApprovalContext` | `{host, protocol}\|null` | ❌ | Présent si c'est une approval réseau. `protocol ∈ {http, https, socks5Tcp, socks5Udp}` |
| `proposedExecpolicyAmendment` | `string[]\|null` | ❌ | Préfixe de commande que Codex suggère de persister comme règle « toujours allow » |
| `proposedNetworkPolicyAmendments` | `{host, action}[]\|null` | ❌ | Suggestion d'ajout de règle hostname pour le réseau |

**Décisions valides** (`/home/twidi/dev/codex/codex-rs/app-server-protocol/schema/typescript/v2/CommandExecutionApprovalDecision.ts`, vérifié) :

```ts
type CommandExecutionApprovalDecision =
    | "accept"                                                              // run once
    | "acceptForSession"                                                    // run + cache en mémoire pour le reste de la session (in-process)
    | { acceptWithExecpolicyAmendment: { execpolicy_amendment: string[] } } // run + écrit la règle dans ~/.codex/rules/default.rules (persisté)
    | { applyNetworkPolicyAmendment: { network_policy_amendment: { host, action: "allow"|"deny" } } }
                                                                            // run + persiste la règle réseau
    | "decline"                                                             // refuse, le modèle peut tenter autre chose
    | "cancel";                                                             // abort le turn entier
```

Sémantique côté Rust mappée vers `ReviewDecision` : `/home/twidi/dev/codex/codex-rs/protocol/src/protocol.rs:3590-3625`.

> ⚠️ **À noter** : le default handler du SDK Python (`src/codex_app_server/client.py:480-485`) renvoie `{"decision": "accept"}` pour `commandExecution`/`fileChange`, et `{}` pour tout le reste. Il n'utilise pas `decline`. Côté TwiCC on enverra explicitement `"decline"` (pas `"deny"`, ce mot n'est pas dans le schema v2) pour les refus user.

#### 1.1.b `item/fileChange/requestApproval`

**Params** (`ServerRequest.json:591-629`, vérifié) :

| Champ | Type | Obligatoire | Description |
|-------|------|-------------|-------------|
| `threadId` | `string` | ✅ | |
| `turnId` | `string` | ✅ | |
| `itemId` | `string` | ✅ | Corrèle avec un item `ApplyPatch` |
| `startedAtMs` | `int64` | ✅ | |
| `reason` | `string\|null` | ❌ | |
| `grantRoot` | `string\|null` | ❌ | `[UNSTABLE]` — demande à autoriser les écritures sous ce path pour la session |

**🔴 Surprise (vérifiée)** : le diff lui-même **n'est pas dans le payload v2**. Seul `itemId` corrèle. Pour afficher le diff dans l'UI il faudra le récupérer depuis l'event `item/started` correspondant au `ApplyPatch` (qui contient bien `changes: [{diff, kind, path}, …]` comme on l'a vu en debug du streaming).

**Décisions** (`v2/FileChangeApprovalDecision.ts`, vérifié) :
```ts
type FileChangeApprovalDecision = "accept" | "acceptForSession" | "decline" | "cancel";
```

Pas de variant `acceptWithAmendment` pour les fichiers. Aucun moyen de modifier le patch avant approval.

#### 1.1.c `item/permissions/requestApproval`

**Params** (`ServerRequest.json:1588-1626`) :

| Champ | Type | Obligatoire | Description |
|-------|------|-------------|-------------|
| `threadId`, `turnId`, `itemId`, `startedAtMs` | (idem) | ✅ | |
| `cwd` | `AbsolutePathBuf` | ✅ | |
| `reason` | `string\|null` | ❌ | Justification modèle |
| `permissions` | `RequestPermissionProfile` | ✅ | Ce que le modèle veut (network + filesystem) |

**Réponse** (`v2/PermissionsRequestApprovalResponse.ts`, vérifié) :

```ts
{
  permissions: GrantedPermissionProfile,  // = ce que TU accordes (peut être un sous-ensemble)
  scope: "turn" | "session",               // valable pour ce turn ou toute la session
  strictAutoReview?: boolean               // si true → chaque commande suivante du turn est forcée en review
}
```

Pour refuser : envoyer `permissions: {}` (objet vide, donc `network` et `fileSystem` absents = zéro permission accordée). **Ne PAS envoyer `permissions: null`** — le champ n'est pas nullable dans le schema TS (`GrantedPermissionProfile` est un objet, pas un union avec null).

> **Décision recommandée pour notre cas** : on peut **ignorer ce 3ème type d'approval pour la v1** et le bloquer côté policy. Justification : le tool `request_permissions` est appelé par le modèle uniquement quand il sent qu'il a besoin de plus que ce que le sandbox lui donne. Tant qu'on tourne en `workspace-write` (le défaut sain), ça doit être rare et le user peut juste `decline`. À confirmer en §7-Q5.

### 1.2 `approval_policy`

Source vérifiée : `/home/twidi/dev/codex/codex-rs/protocol/src/protocol.rs:889-920` et SDK Python `src/codex_app_server/generated/v2_all.py:220-258`.

| Valeur (wire) | Comportement |
|---------------|-------------|
| `"untrusted"` | Seules les commandes « known safe read-only » passent. Tout le reste → prompt user. |
| `"on-failure"` | **Deprecated**. Exécute en sandbox, prompt seulement si la sandbox cause un échec. |
| `"on-request"` *(défaut)* | Le modèle décide. Prompt seulement quand `Restricted` + policy `Prompt`. |
| `{"granular": {sandbox_approval, rules, skill_approval, request_permissions, mcp_elicitations}}` | Fine-grained, chaque bool = afficher prompt ou auto-reject. |
| `"never"` | Jamais de prompt. Toute action qui aurait dû prompt est **silently rejected** (erreur retournée au modèle). |

**Scope** : per-thread, sticky d'un turn à l'autre. Peut être **changée live** en passant `approvalPolicy` au prochain `turn/start`. Source : `TurnStartParams` (`/home/twidi/dev/codex/codex-rs/app-server-protocol/src/protocol/v2/turn.rs:49-119`).

### 1.3 `sandbox` (SandboxMode)

**Vérifié** : l'énum `SandboxMode` du SDK Python (`src/codex_app_server/generated/v2_all.py:3194-3197`) n'a que **3 valeurs**. C'est ce qu'accepte le param `sandbox` de `thread_start`/`thread_resume`/`thread_fork`.

| Valeur | Sens |
|--------|------|
| `"read-only"` | Sandbox active, écriture interdite. |
| `"workspace-write"` *(défaut sain)* | Sandbox active, écriture autorisée dans `writableRoots` (par défaut le project root). |
| `"danger-full-access"` | Pas de sandbox. C'est notre valeur actuelle (bypass). |

> **Note** : il existe AUSSI un type `SandboxPolicy` (RootModel à 4 variants au lieu d'enum) qui inclut `"externalSandbox"` (sandbox externe type Docker). Ce type n'est **pas** ce que `thread_start(sandbox=…)` accepte ; il est utilisé par `TurnStartParams.sandbox_policy` pour overrider en cours de turn avec un payload plus riche. On n'en a pas besoin pour notre v1.

**Important** : sandbox et approval_policy sont **orthogonaux**. Sandbox dit *ce qui est interdit physiquement*, approval_policy dit *quand demander*. Avec `"never"` + `"workspace-write"`, une commande qui veut écrire hors workspace **échoue silencieusement** dans la sandbox (erreur au modèle), elle n'est pas escaladée. Vérifié dans `core/src/tools/sandboxing.rs:199-239`.

### 1.4 Trust rules / persistance

**Pas de RPC séparé pour ajouter une règle de confiance live.** Le seul mécanisme : retourner `acceptWithExecpolicyAmendment` (commandes) ou `applyNetworkPolicyAmendment` (réseau) dans la réponse à un approval. Le serveur Codex écrit alors une ligne dans `~/.codex/rules/default.rules` (ou `.codex/rules/` au niveau projet) **et** hot-reload la policy en mémoire.

Source : `core/src/exec_policy.rs:381-475`. Format de fichier :

```
prefix_rule(pattern=["git", "status"], decision="allow")
network_rule(host="api.github.com", protocol="https", decision="allow")
```

Le `acceptForSession` reste en RAM (in-process `ApprovalStore`, perdu au prochain run).

**Préfixes bannis de suggestion** (`core/src/exec_policy.rs:52-99`) : `python3`, `bash`, `sh`, `git`, `node`, `sudo`, etc. Pour ces préfixes, le serveur ne mettra jamais `proposedExecpolicyAmendment` dans la requête — c'est trop risqué de les whitelister automatiquement.

### 1.5 `availableDecisions` — possible mais non confirmé v2

L'agent 1 mentionne un champ `availableDecisions: CommandExecutionApprovalDecision[]\|null` dans `CommandExecutionRequestApprovalParams`. **Pas trouvé dans le schema actuel** (`ServerRequest.json:345-442` vérifié). C'est probablement une version plus récente du protocole. À ignorer pour l'instant ; côté UI on affichera systématiquement le set complet de décisions et on laissera Codex rejeter celles qu'il n'accepte pas. Si on tombe sur des rejets, on creusera.

### 1.6 Autres server requests qui transitent par `_handle_server_request`

Notre approval handler **intercepte toutes les server requests** (méthodes `with id`), pas seulement les 3 approvals. Le schema en répertorie plusieurs autres dans `ServerRequest.json` (vérifié) :

- `item/tool/call` (ligne 1883) — appel de tool dynamique (dispatché par le serveur Codex au client pour les tools "dynamiques" type MCP). Si on supporte MCP, ces requests passeront chez nous.
- `account/chatgptAuthTokens/refresh` (ligne 1920+) — demande de refresh OAuth.
- `item/tool/requestUserInput` (ligne 1800) — formulaire user-input (experimental).
- `mcpServer/elicitation/request` (ligne 1826) — formulaire MCP.

**Implication pour notre handler** : retourner `{}` pour ces méthodes va probablement casser le SDK (le serveur Codex attend un payload typé). Notre `_sync_approval_handler` doit :

- Reconnaître explicitement les 3 méthodes d'approval qu'on gère.
- Pour toute autre méthode : **logger un warning** (`logger.warning("Unhandled Codex server request: %s", method)`) puis déléguer au handler d'origine du SDK (`_default_approval_handler`, capturé avant le monkey-patch). Le default renvoie `{"decision": "accept"}` pour les 2 méthodes d'approval qu'il connaît, `{}` ailleurs.
- En pratique en v1, on ne déclenchera pas MCP/dynamic tools/elicitations dans nos sessions ; le warning est là pour qu'on s'en aperçoive si ça arrive un jour (cf. Q9 § 7).

Le pattern :
```python
# Capture in __init__ before monkey-patch
self._sdk_default_approval_handler = self._codex._client._sync._approval_handler

# Inside _sync_approval_handler
if not is_approval_method(method):
    logger.warning("Unhandled Codex server request: %s", method)
    return self._sdk_default_approval_handler(method, params)
```

### 1.7 Events liés mais hors scope

- `item/autoApprovalReview/{started,completed}` : notifications informatives quand le guardian auto-review est actif. Avec `approvalsReviewer="user"` (notre choix), elles ne sont **pas émises**. À ignorer dans le agent stream handler.
- `thread/approveGuardianDeniedAction` : RPC pour override une décision guardian. Pas notre cas.

---

## 2. SDK Python vendored — où se branche le handler

### 2.1 Signature et type

`src/codex_app_server/client.py:50` (vérifié) :
```python
ApprovalHandler = Callable[[str, JsonObject | None], JsonObject]
```

**Synchrone**. Reçoit `(method, params)`, renvoie un `JsonObject` dict (pas typé Pydantic — c'est du raw `{"decision": "accept"}`, etc.).

### 2.2 Branchement sync — où le handler est appelé

`client.py:504-512` (vérifié) :
```python
def _handle_server_request(self, msg: dict[str, JsonValue]) -> JsonObject:
    method = msg["method"]
    params = msg.get("params")
    if not isinstance(method, str):
        return {}
    return self._approval_handler(
        method,
        params if isinstance(params, dict) else None,
    )
```

Appelé dans deux read-loops :
- `_request_raw` (client.py:241-272) : quand on attend une réponse RPC, on consume les messages intermédiaires, et tout server request reçu est dispatché au handler.
- `next_notification` (client.py:277-288) : pareil quand on consume le stream.

**Conséquence** : pendant un `turn_handle.stream()`, si le serveur Codex envoie `item/commandExecution/requestApproval`, le SDK Python **bloque** son read loop jusqu'à ce que `_approval_handler` retourne — et écrit la réponse JSON-RPC tout de suite.

### 2.3 Branchement côté `AsyncAppServerClient` — PIÈGE

`src/codex_app_server/async_client.py:39-43` (vérifié) :
```python
class AsyncAppServerClient:
    def __init__(self, config: AppServerConfig | None = None) -> None:
        self._sync = AppServerClient(config=config)
        # ...
```

**`AsyncAppServerClient.__init__` n'accepte PAS `approval_handler`.** Il construit toujours `AppServerClient(config=config)` sans handler ⇒ on hérite du default (auto-accept tout).

Les méthodes `thread_start`, `turn_start`, etc., font ensuite `await asyncio.to_thread(self._sync.xxx, ...)` (`_call_sync`, async_client.py:54-62). Donc le sync handler **tourne dans une thread worker** du pool `asyncio.to_thread`.

#### Options pour brancher notre handler

| Option | Approche | Pour | Contre |
|--------|---------|------|--------|
| **A** : monkey-patch | Après création du `AsyncCodex`, faire `codex._client._sync._approval_handler = our_handler` | Minimal, n'impacte pas le SDK vendored | Touche un attribut privé ; doit être refait à chaque thread reset |
| **B** : sous-classe locale | `TwiccAsyncAppServerClient(AsyncAppServerClient)` qui surcharge `__init__` et passe un handler au `AppServerClient` interne | Plus propre, type-safe | Doit aussi sous-classer `AsyncCodex` pour injecter notre client custom, qui lui prend déjà un client en interne |
| **C** : patcher le SDK vendored | Ajouter `approval_handler=None` au `__init__` de `AsyncAppServerClient` | Le plus naturel | Modifier du code vendored, à re-patcher à chaque update du SDK ; va dans la mémoire `reference_sdk_update_procedure.md` |
| **D** : forker `AsyncCodex` côté TwiCC | Construire manuellement notre stack `AppServerClient(handler=…) → wrapper async → AsyncThread` | Total contrôle | Re-implémente trop de chose |

**Ma recommandation : option A**, avec un commentaire qui dit clairement « API privée du SDK vendored, refaire si on monte de version ». Trivial et n'impose pas de fork. Si une future update du SDK rend ça impossible on bascule vers C.

À trancher en §7-Q2.

### 2.4 Pont sync → async pour la réponse

Notre vrai handler doit attendre un click user (async). Le SDK appelle un callable sync depuis une thread worker. Pattern requis :

```python
def _codex_approval_handler_sync(method: str, params: JsonObject | None) -> JsonObject:
    """Called from a SDK worker thread. Schedules the async handler on the main loop."""
    coro = self._codex_approval_handler_async(method, params)
    fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
    return fut.result()  # blocks the worker thread until the coroutine resolves
```

Le main loop voit la coroutine, broadcast le `PendingRequest`, attend le user, résout la Future, la coroutine renvoie le dict de décision, la worker thread débloque et envoie la réponse JSON-RPC.

**Risques à gérer** :
- Si on kill l'agent pendant qu'on attend, le sync `fut.result()` reste bloqué pour l'éternité. Solution : sur `interrupt_or_kill`, on résout d'abord toutes les pending futures avec une décision `cancel` ou `decline`, AVANT de fermer le transport.
- Si la coroutine lève, `fut.result()` la re-raise dans la worker thread, le SDK voit l'exception comme un échec du handler. À tester ce que ça donne côté Codex — probablement le serveur attend toujours, donc en pratique on doit **toujours** retourner un dict, jamais lever.

---

## 3. État de TwiCC aujourd'hui

### 3.1 `PendingRequest` est déjà provider-neutral

`src/twicc/agent/states.py:38-54` (vérifié) :
```python
@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    request_type: str                          # "tool_approval" | "ask_user_question"
    tool_name: str
    tool_input: dict
    created_at: float
    permission_suggestions: list[dict] | None = None
```

- Le dataclass vit dans `twicc.agent` (provider-neutral).
- `AgentInfo.pending_requests: tuple[PendingRequest, ...]` (`states.py:96`) — porté par le snapshot que chaque agent broadcast.
- `serialize_agent_info` (`states.py:108-142`) sérialise déjà ces fields.

**Conséquence** : on peut **réutiliser tel quel** pour Codex. Pour les approvals Codex, on emballera le payload Codex dans `tool_input` et on mettra :
- `request_type = "tool_approval"` (pas de `ask_user_question` côté Codex)
- `tool_name` ∈ `{"commandExecution", "fileChange", "permissions"}`
- `permission_suggestions = None`
- (et un éventuel champ supplémentaire si on a besoin — voir §4 et §7-Q4)

### 3.2 La plomberie Future + WS — encore CC-spécifique mais facilement portable

Localisée sur `ClaudeCodeAgent` :

| Fonction / state | Fichier:Ligne | Provider-spec ? |
|------------------|--------------|----------------|
| `_pending_requests: dict[str, PendingRequest]` | `claude_code/agent/agent.py:125` | ❌ Générique |
| `_pending_futures: dict[str, asyncio.Future[…]]` | `claude_code/agent/agent.py:126` | ⚠️ Type-paramétré (Allow/Deny CC) |
| `pending_requests` property | `claude_code/agent/agent.py:181` | ❌ Générique |
| `get_info()` override | `claude_code/agent/agent.py:311` | ❌ Générique |
| `_handle_pending_request(tool_name, input_data, context)` | `claude_code/agent/agent.py:541-634` | ⚠️ Signature SDK-spec |
| `resolve_pending_request(request_id, response)` | `claude_code/agent/agent.py:636-674` | ⚠️ Type response SDK-spec |
| `_cancel_pending_request_future()` | `claude_code/agent/agent.py:676-686` | ❌ Générique |
| Manager `resolve_pending_request` | `claude_code/agent/manager.py:319-343` | ❌ Générique au pattern |
| Manager timeout skip si `pending_requests` | `claude_code/agent/manager.py:430` | ❌ Générique |

### 3.3 Le frontend partage déjà la majorité

| Élément | Fichier | Status |
|---------|---------|--------|
| `PendingRequestForm.vue` | `frontend/src/components/message/PendingRequestForm.vue` | ⚠️ Hardcode `import respondToPendingRequest from '../../providers/claude_code/ws'` (l. 9) + permission_suggestions UI (l. 635-666) |
| `store.getPendingRequests(sessionId)` | `frontend/src/stores/data.js:511-512` | ✅ Générique |
| `processStates[id].pending_requests` | `frontend/src/stores/data.js:2539` | ✅ Générique |
| Toast notification | `frontend/src/composables/useWebSocket.js:450-474` | ✅ Utilise `providerLabel`, déjà multi-provider |
| Mount dans `SessionItemsList.vue` | `frontend/src/components/session/detail/SessionItemsList.vue:1566-1571` | ✅ Conditionnel sur `hasPendingRequest`, agnostique |
| `respondToPendingRequest` | `frontend/src/providers/claude_code/ws.js:33-40` | ⚠️ Le message type est `claude_code:pending_request_response` (provider-prefixed) |

### 3.4 Le bypass actuel Codex

Trois sites de bypass :

| Fichier:Ligne | Code | À modifier ? |
|---------------|------|--------------|
| `src/twicc/providers/codex/agent/manager.py:227-228` | `approval_policy = AskForApproval.model_validate("never"); sandbox = SandboxMode.danger_full_access` | ✅ Oui — c'est l'object du chantier |
| `src/twicc/providers/codex/credentials.py:300` | Même bypass dans `_codex_sdk_throwaway_call` | ❌ À garder — appel interne pour rafraîchir un token, jamais user-facing |
| `src/twicc/providers/codex/title_suggest.py:152` | Même bypass pour la génération de titre | ❌ À garder — turn interne sans tool calls |

### 3.5 Settings Codex côté frontend — préset modes (architecture intentionnelle)

`frontend/src/providers/codex/constants.js:14-23` (vérifié) :
```js
// Mirrors the Codex CLI's approval modes (read-only / auto / autonomous / yolo).
export const PERMISSION_MODE = {
    READ_ONLY: 'read_only',    // underscore — pas le hyphen du SandboxMode wire value
    AUTO: 'auto',
    AUTONOMOUS: 'autonomous',
    YOLO: 'yolo',
}
```

`frontend/src/providers/codex/helpers.js:66-87` expose les 4 modes dans `AGENT_SETTINGS_CHOICES`.

**Intention architecturale** : ces 4 (bientôt 5 avec `strict`, voir §4 Étape 7) sont des **présets orthogonaux** qui combinent `sandbox_mode` + `approval_policy` en un seul setting user-friendly. Plutôt que d'exposer deux settings techniques au user, on lui donne un choix sémantique ("read-only", "auto", "yolo"…) et le backend traduit en config Codex.

Le backend actuel (`manager.py:227-228`) **ignore** la valeur et force `"never"` + `"danger-full-access"` parce que le mapping n'a pas encore été câblé — c'est ce qu'on va corriger dans Étape 7. La présence du sélecteur dans le frontend est correcte ; il attendait juste son wiring.

> ⚠️ **Piège mapping wire-format** : le frontend stocke `'read_only'` (underscore), mais le `SandboxMode.read_only` du SDK Python sérialise vers `"read-only"` (hyphen). Tout mapping `PERMISSION_MODE → SandboxMode` doit normaliser.

---

## 4. Plan d'implémentation

Découpé en étapes ordonnées. Chaque étape est self-contained et testable. On peut commit après chaque étape.

### Étape 1 — Factoriser les pending requests dans `BaseAgent` et `BaseAgentManager`

**But** : remonter la plomberie générique (dict in-flight, Future, get_info override, cancel-on-kill, resolve_pending_request du manager) pour que `ClaudeCodeAgent` et `CodexAgent` n'aient plus que la partie provider-spec.

#### 1.1 `BaseAgent` (`src/twicc/agent/base_agent.py`) gagne :

```python
# Dans __init__
self._pending_requests: dict[str, PendingRequest] = {}
self._pending_futures: dict[str, asyncio.Future[Any]] = {}

@property
def pending_requests(self) -> tuple[PendingRequest, ...]:
    return tuple(sorted(self._pending_requests.values(), key=lambda r: r.created_at))

# get_info() devient :
def get_info(self) -> AgentInfo:
    base = AgentInfo(... existing fields ...)
    return base._replace(pending_requests=self.pending_requests)

# Nouveau : la mécanique générique
async def _await_pending_request(
    self,
    request: PendingRequest,
) -> Any:
    """Register a pending request, broadcast, wait for resolution, return raw response."""
    self._pending_requests[request.request_id] = request
    future = asyncio.get_running_loop().create_future()
    self._pending_futures[request.request_id] = future
    await self._notify_state_change()
    try:
        return await future
    finally:
        self._pending_requests.pop(request.request_id, None)
        self._pending_futures.pop(request.request_id, None)
        await self._notify_state_change()

def _cancel_all_pending_futures(self) -> None:
    """Cancel every in-flight Future. The awaiter in each provider handles cleanup."""
    for fut in self._pending_futures.values():
        if not fut.done():
            fut.cancel()
    # Don't clear dicts here — let the awaiters clear them in their finally
```

**Comportement identique entre Claude et Codex** :
- Côté Claude, le `await future` lève `CancelledError`, le SDK rattrape, fin.
- Côté Codex, le `await future` lève `CancelledError` dans la coroutine async, propage jusqu'au `run_coroutine_threadsafe(...).result()` qui re-raise dans la worker thread, puis le wrapper sync handler attrape et retourne `default_response_for(method)` au SDK (cf. Étape 2 §2.3).

#### 1.2 `BaseAgent.resolve_pending_request` générique :

```python
def resolve_pending_request(self, request_id: str, response: Any) -> bool:
    future = self._pending_futures.get(request_id)
    if future is None or future.done():
        return False
    future.set_result(response)
    return True
```

#### 1.3 `BaseAgentManager` (`src/twicc/agent/base_manager.py`) gagne :

```python
async def resolve_pending_request(
    self, session_id: str, request_id: str, response: Any,
) -> bool:
    agent = self._agents.get(session_id)
    if agent is None:
        return False
    return agent.resolve_pending_request(request_id, response)
```

Et le timeout monitor doit skipper les agents qui ont `pending_requests` (déjà le cas dans claude_code/agent/manager.py:430, à remonter).

#### 1.4 Côté `ClaudeCodeAgent` et `ClaudeCodeAgentManager`

- Retire les dicts et property locaux (utilise ceux de BaseAgent).
- `_handle_pending_request` continue d'exister mais devient un thin wrapper sur `_await_pending_request` qui :
  - Construit le `PendingRequest` (avec permission_suggestions)
  - Appelle `_await_pending_request`
  - Cast le response en `PermissionResultAllow | PermissionResultDeny`
- `_cancel_pending_request_future()` devient `_cancel_all_pending_futures(default_response=PermissionResultDeny(message="agent stopped"))` — ou un raw cancel si on préfère.

### Étape 2 — Le `CodexApprovalBridge`

Composant qui vit sur `CodexAgent` (ou un mixin) et fait le pont sync ↔ async.

#### 2.1 Localisation

Soit dans `src/twicc/providers/codex/agent/approvals.py` (nouveau fichier), soit en méthodes privées dans `agent.py`. Je préfère un fichier séparé parce qu'il y aura ~150-200 lignes de mapping decision/payload.

#### 2.2 Skeleton

```python
# src/twicc/providers/codex/agent/approvals.py
from __future__ import annotations
import asyncio
import time
import uuid
import logging
from typing import Any
from twicc.agent.states import PendingRequest

logger = logging.getLogger(__name__)

# Wire method → human-readable tool_name we expose in PendingRequest
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "commandExecution",
    "item/fileChange/requestApproval": "fileChange",
    "item/permissions/requestApproval": "permissions",
}

# Sentinel response used when we have to release the SDK on kill without a real user click.
# We send `decline` for individual approvals (safer than `cancel` which aborts the turn).
DEFAULT_KILL_RESPONSE = {"decision": "decline"}
DEFAULT_KILL_PERMISSIONS_RESPONSE = {
    "permissions": {},
    "scope": "turn",
}

def is_approval_method(method: str) -> bool:
    return method in APPROVAL_METHODS

def derive_request_id(params: dict | None) -> str:
    """Stable key for routing the future.

    For sub-command approvals (zsh-exec-bridge), a single ``itemId`` can fan out
    into several callbacks each carrying their own ``approvalId`` (vérifié dans
    ``ServerRequest.json:345-442`` description). So we key by approvalId when
    present, falling back to itemId.
    """
    if not params:
        return str(uuid.uuid4())
    return params.get("approvalId") or params.get("itemId") or str(uuid.uuid4())

def make_pending_request(method: str, params: dict | None) -> PendingRequest:
    tool_name = APPROVAL_METHODS[method]
    return PendingRequest(
        request_id=derive_request_id(params),
        request_type="tool_approval",
        tool_name=tool_name,
        tool_input=params or {},
        created_at=time.time(),
        permission_suggestions=None,
    )

def default_response_for(method: str) -> dict:
    """The response we send when the Future is cancelled (kill, transport error).
    Used by the sync handler's CancelledError branch — never called from interrupt_or_kill.
    """
    if method == "item/permissions/requestApproval":
        return DEFAULT_KILL_PERMISSIONS_RESPONSE
    return DEFAULT_KILL_RESPONSE
```

#### 2.3 Wiring dans `CodexAgent`

```python
# src/twicc/providers/codex/agent/agent.py
class CodexAgent(BaseAgent):
    def __init__(self, ..., codex: AsyncCodex, ...):
        super().__init__(...)
        self._codex = codex
        # _loop captured lazily in start() (must be a RUNNING loop — get_event_loop()
        # is deprecated in 3.12+ and returns the wrong loop if called outside async).
        self._loop: asyncio.AbstractEventLoop | None = None
        # Capture the SDK's default handler so we can delegate non-approval server requests.
        self._sdk_default_approval_handler = (
            self._codex._client._sync._approval_handler
        )
        # Monkey-patch the SDK's private sync handler. See analysis-codex-approvals.md §2.3.
        self._codex._client._sync._approval_handler = self._sync_approval_handler

    async def start(self, ...):
        # Capture the running loop right before we kick off any turn — this is the
        # loop the SDK worker threads will dispatch approval callbacks back to.
        self._loop = asyncio.get_running_loop()
        # ... rest of start()

    def _sync_approval_handler(self, method: str, params: dict | None) -> dict:
        """Called by the SDK from a worker thread (via asyncio.to_thread). Bridges to async."""
        if not is_approval_method(method):
            # Defensive fallback: let the SDK's default handle other server requests
            # (item/tool/call, account/chatgptAuthTokens/refresh, ...). See §1.6.
            return self._sdk_default_approval_handler(method, params)

        if self._loop is None or self._loop.is_closed():
            logger.error("Codex approval received before loop init or after close: %s", method)
            return default_response_for(method)

        coro = self._async_approval_handler(method, params)
        try:
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
        except asyncio.CancelledError:
            # Future was cancelled (kill, restart, transport error). Convert the
            # cancellation into a safe wire response so the SDK doesn't hang.
            return default_response_for(method)
        except Exception as exc:
            logger.error("Codex approval bridge failed for %s: %s", method, exc, exc_info=True)
            return default_response_for(method)

    async def _async_approval_handler(self, method: str, params: dict | None) -> dict:
        request = make_pending_request(method, params)
        response = await self._await_pending_request(request)
        # response is the dict the frontend sent (passed verbatim by resolve_pending_request).
        # We trust the WS handler to have already shape-validated it (see §7-Q11 + Étape 3).
        return response
```

#### 2.4 Cancel on kill — chain réaction

`AsyncAppServerClient.close()` (`async_client.py:76-77`) appelle `_call_sync(self._sync.close)` qui prend `self._transport_lock`. **Or, pendant qu'un approval est in-flight, le lock est tenu par le worker thread bloqué sur `fut.result()` dans notre bridge sync.** ⇒ Un `await codex.close()` direct serait en **deadlock** sur le lock.

La résolution est **identique au cas Claude** : on `future.cancel()` tout, et la cascade fait le travail :

```python
async def interrupt_or_kill(self, reason: str) -> None:
    if self.state == AgentState.DEAD:
        return
    self.kill_reason = reason

    # 1. Cancel every pending approval Future.
    #    Cascade per pending approval:
    #    a) future.cancel() → await future in _await_pending_request raises CancelledError
    #    b) The async coroutine propagates the exception (its finally cleans up dicts)
    #    c) run_coroutine_threadsafe(...).result() re-raises in the SDK worker thread
    #    d) Our _sync_approval_handler catches asyncio.CancelledError, returns
    #       default_response_for(method) — wire response sent to Codex
    #    e) Worker thread continues read loop, _call_sync's async-with releases _transport_lock
    self._cancel_all_pending_futures()  # inherited from BaseAgent

    # 2. Now close — the lock should free within a few ms once all workers unwound.
    try:
        await asyncio.wait_for(self._codex.close(), timeout=5.0)
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("codex.close() failed/timed out: %s — killing subprocess", e)
        # Last-resort escape: kill the underlying subprocess directly, bypassing the lock.
        proc = self._codex._client._sync._proc
        if proc is not None:
            proc.kill()

    # 3. Transition state, notify, etc.
    self._set_state(AgentState.DEAD)
    ...
```

**Aucune différence sémantique avec Claude** sur le kill path : `future.cancel()` partout. La spécificité Codex est dans le sync handler (catch CancelledError + return default wire response), pas dans le manager.

### Étape 3 — Le WS handler côté Codex

`src/twicc/providers/codex/ws.py` doit gagner un dispatch `pending_request_response`. Mirror complet du Claude.

```python
# src/twicc/providers/codex/ws.py

class CodexWSHandler(BaseProviderWSHandler):
    async def dispatch(self, action: str, content: dict) -> None:
        ...
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return
        ...

    async def _handle_pending_request_response(self, content: dict) -> None:
        session_id = content.get("session_id")
        request_id = content.get("request_id")
        decision = content.get("decision")
        # Codex doesn't have permission_suggestions / updated_permissions / updated_input.
        # We just take a raw decision payload and forward.

        if not session_id or not request_id or decision is None:
            logger.warning("codex pending_request_response missing fields: %r", content)
            return

        # Build the raw dict expected by the Codex SDK
        response = self._build_codex_response(content)

        manager = registry.get_manager(Provider.CODEX)
        resolved = await manager.resolve_pending_request(session_id, request_id, response)
        if not resolved:
            logger.warning(
                "codex pending_request_response: failed to resolve %s for %s",
                request_id, session_id,
            )

    def _build_codex_response(self, content: dict) -> dict:
        """Convert the frontend's payload into the SDK-wire response dict."""
        decision = content["decision"]
        tool_name = content.get("tool_name")  # we'll need this from the frontend

        if tool_name == "permissions":
            # frontend sends: {decision: "accept"|"decline", permissions?, scope?}
            if decision == "decline":
                return {"permissions": {}, "scope": "turn"}
            return {
                "permissions": content.get("permissions", {}),
                "scope": content.get("scope", "turn"),
            }

        # command + file change use the same shape
        if isinstance(decision, dict):
            # acceptWithExecpolicyAmendment / applyNetworkPolicyAmendment
            return {"decision": decision}
        # Simple string: accept | acceptForSession | decline | cancel
        return {"decision": decision}
```

### Étape 4 — Le stream handler dans `CodexAgent._handle_stream_event`

Aujourd'hui les approval methods ne sont pas dans le stream — elles arrivent comme server-requests synchrones interceptées par `_handle_server_request`. Donc le stream handler **n'a rien à faire de plus**. Au max, ignorer explicitement `item/autoApprovalReview/{started,completed}` pour ne pas tout faire planter si on les reçoit.

Mais : il y a une **question d'ordering**. Le `item/started` pour un `commandExecution` ou un `fileChange` arrive normalement AVANT le `item/commandExecution/requestApproval` correspondant. On voudra peut-être :
- Soit afficher la PendingRequest dans la même carte que le tool, en mode "en attente d'approbation".
- Soit ne pas afficher l'item du tout tant que l'approval n'est pas accordée.

Voir §7-Q6.

**Side-table itemId→payload pour le diff de `fileChange`** : la `PendingRequest` pour un fileChange ne contient PAS le diff (vérifié §1.1.b — le schema v2 ne le transporte pas). Mais l'event `item/started` pour ce même `ApplyPatch` item (qu'on a déjà observé dans les logs : `{changes: [{diff, kind, path}, …]}`) l'envoie. Pour que l'UI puisse afficher le diff sur la card d'approval, il faut indexer ces payloads :

```python
# Dans CodexAgent (provider-specific)
self._items_by_id: dict[str, dict] = {}  # itemId → raw inner payload from item/started

# Dans _handle_stream_event, sur item/started:
if method == "item/started":
    item = getattr(payload, "item", None)
    inner = getattr(item, "root", item)
    item_id = getattr(inner, "id", None)
    if item_id:
        self._items_by_id[item_id] = inner.model_dump(mode="json", by_alias=True)

# Au moment du make_pending_request, on injecte le payload connu si dispo:
def make_pending_request(method, params, items_by_id):
    pending = PendingRequest(
        ...
        tool_input={
            **params,
            "_item_payload": items_by_id.get(params.get("itemId")),  # may be None
        },
        ...
    )
```

Sur `item/completed`, on peut nettoyer `_items_by_id.pop(item_id, None)` pour ne pas accumuler.

Alternative plus propre : faire un fetch on-demand côté frontend via le store des items déjà reçus en streaming (le store data.js a déjà ces items en visualItems). Mais c'est plus de couplage UI/state. Décision à prendre en §7-Q10.

### Étape 5 — Frontend : factoriser `PendingRequestForm.vue`

#### 5.1 Découpler le `respondToPendingRequest`

Aujourd'hui (`PendingRequestForm.vue:9`) :
```js
import { respondToPendingRequest as respondToClaudeCodePendingRequest }
    from '../../providers/claude_code/ws'
```

Refonte : un dispatcher provider-agnostique dans `frontend/src/providers/index.js` (déjà présent) qui route :

```js
// frontend/src/providers/index.js
import { respondToPendingRequest as respondClaudeCode } from './claude_code/ws'
import { respondToPendingRequest as respondCodex }     from './codex/ws'

export function respondToPendingRequest(provider, sessionId, requestId, responseData) {
    if (provider === 'claude_code') return respondClaudeCode(sessionId, requestId, responseData)
    if (provider === 'codex')       return respondCodex(sessionId, requestId, responseData)
    throw new Error(`Unknown provider: ${provider}`)
}
```

Et le composant :
```js
import { respondToPendingRequest } from '../../providers'
// ...
respondToPendingRequest(session.provider, sessionId, requestId, responsePayload)
```

#### 5.2 Provider-aware rendering

Le composant a déjà des branches `tool_approval` vs `ask_user_question`. On peut :

- Garder le composant principal `PendingRequestForm.vue` comme orchestrateur (props, state, WS send).
- Extraire le contenu (résumé du tool, JsonHuman view, boutons spécifiques) en **sous-composants par provider** dans :
  - `frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue` (existant logique CC + permission_suggestions)
  - `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` (nouveau)
- Dispatch dans le parent :
  ```html
  <CodexPendingRequestBody v-if="provider === 'codex'" :request="pendingRequest" ... />
  <ClaudePendingRequestBody v-else-if="provider === 'claude_code'" ... />
  ```

Côté boutons Codex : 4 boutons selon le tool_name :

- **commandExecution** : Approve / Approve & remember (acceptForSession) / Approve & add allow rule (si `proposedExecpolicyAmendment` ou `proposedNetworkPolicyAmendments` présents) / Deny / Cancel turn
- **fileChange** : Approve / Approve all in session / Deny / Cancel turn
- **permissions** : (voir §7-Q5 — peut-être laisser deny-only)

Ces boutons construisent la `decision` payload selon les variants du schema TS rappelés en §1.1.a/b/c.

#### 5.3 Affichage des informations

Pour commandExecution :
- Header : la commande shell-joinée + cwd
- Si `commandActions` présent : afficher l'intent ("Read file X", "List files Y", "Search Z")
- Si `networkApprovalContext` : afficher "wants network access to {host} via {protocol}"
- Si `reason` : montrer la justification
- Si `proposedExecpolicyAmendment` : checkbox "Add `git status` to allow list permanently"

Pour fileChange :
- Header : "Wants to apply patch to {N} file(s)"
- Body : afficher le diff issu de l'item `ApplyPatch` correspondant (via `itemId` corrélé). Donc le store doit pouvoir indexer les items en cours par `itemId`.

Pour permissions : voir §7-Q5.

### Étape 6 — Mise à jour de la WS protocol

Le message inbound côté frontend → backend devient :

**Claude (déjà en place)** :
```json
{
    "type": "claude_code:pending_request_response",
    "session_id": "...",
    "request_id": "...",
    "decision": "allow" | "deny",
    "updated_input": {…},
    "updated_permissions": [{…}],
    "deny_reason": "..."
}
```

**Codex (nouveau)** :
```json
{
    "type": "codex:pending_request_response",
    "session_id": "...",
    "request_id": "...",
    "tool_name": "commandExecution" | "fileChange" | "permissions",
    "decision": "accept" | "acceptForSession" | "decline" | "cancel"
                | {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": [...]}}
                | {"applyNetworkPolicyAmendment": {"network_policy_amendment": {...}}},
    // Pour "permissions" only :
    "permissions": {…},
    "scope": "turn" | "session"
}
```

### Étape 7 — Settings Codex backend ↔ frontend : câbler le `permission_mode`

Décision actée (cf. §7-Q1) : on **garde** le sélecteur frontend et on lui donne enfin du sens. Chaque préset est traduit côté backend en couple `(sandbox_mode, approval_policy)` au moment du `thread_start`/`thread_resume`.

**Mapping cible** (5 modes — on ajoute `strict` pour rapprocher le comportement du `don't ask` Claude) :

| Mode (wire) | `sandbox_mode` | `approval_policy` | Demande ? | Peut écrire ? | Usage |
|-------------|----------------|-------------------|-----------|---------------|-------|
| `read_only` | `read-only` | `on-request` | ✅ pour toute action hors lecture | ❌ | Exploration assistée |
| `strict` | `read-only` | `never` | ❌ silencieux | ❌ | Lecture pure sans prompt |
| `auto` *(défaut)* | `workspace-write` | `on-request` | ✅ pour sortir du workspace ou réseau | ✅ workspace | Dev courant |
| `autonomous` | `workspace-write` | `never` | ❌ silencieux | ✅ workspace | Tâches longues automatisées |
| `yolo` | `danger-full-access` | `never` | ❌ | ✅ partout | Container jetable |

Note : `strict` et `read_only` se ressemblent — seule différence : `prompt` vs `refus silencieux`. La nuance est utile (équivalent fonctionnel du Claude `don't ask`) mais reste fine.

**Code de mapping** (côté backend, par ex. `src/twicc/providers/codex/agent/manager.py` ou un nouvel `src/twicc/providers/codex/permission_modes.py`) :

```python
from codex_app_server import AskForApproval, SandboxMode

# Wire value (snake_case, what the frontend stores in Session.permission_mode)
# → (SandboxMode enum, AskForApproval enum)
_PRESET_MAP: dict[str, tuple[SandboxMode, AskForApproval]] = {
    "read_only":  (SandboxMode.read_only,          AskForApproval("on-request")),
    "strict":     (SandboxMode.read_only,          AskForApproval("never")),
    "auto":       (SandboxMode.workspace_write,    AskForApproval("on-request")),
    "autonomous": (SandboxMode.workspace_write,    AskForApproval("never")),
    "yolo":       (SandboxMode.danger_full_access, AskForApproval("never")),
}
# CANONICAL DEFAULT after full rollout. PR2a SHIPS with "yolo" so existing
# Codex sessions keep behaving like the current bypass. PR2b flips this to "auto".
DEFAULT_MODE = "auto"

def resolve_codex_policy(mode: str | None) -> tuple[SandboxMode, AskForApproval]:
    return _PRESET_MAP.get(mode or DEFAULT_MODE, _PRESET_MAP[DEFAULT_MODE])
```

> ⚠️ **PR-level value of `DEFAULT_MODE`** :
> - **PR2a** ships with `DEFAULT_MODE = "yolo"` (equivalent to the current bypass — no user-visible change).
> - **PR2b** flips it to `"auto"` (= `workspace-write` + `on-request`) as the new permanent default.
> - **PR3** adds `"strict"` to the preset map (frontend constants.js + the entry above).

À insérer dans `_create_agent` du manager (qui hardcode aujourd'hui `never`/`danger-full-access`).

**Côté frontend** :

1. Ajouter `STRICT: 'strict'` dans `frontend/src/providers/codex/constants.js`.
2. Ajouter l'entrée correspondante dans `AGENT_SETTINGS_CHOICES` de `helpers.js` (avec un label + help text).
3. Vérifier que le rendu Pinia/Vue picker affiche les 5 modes. La sélection se synchronise déjà via `Session.permission_mode` (column existante).

**Migration des sessions existantes** : aucune. La column `permission_mode` accepte n'importe quelle string, les sessions actuelles avec une valeur Codex auront simplement leur mode appliqué pour la première fois au prochain turn. Pour les sessions qui ont `null` : on tombe sur `DEFAULT_MODE = "auto"`.

### Étape 8 — Tests / docs

- Tests unitaires sur le mapping decision Codex (helper `_build_codex_response`).
- Tests sur le bridge sync/async — à voir comment mocker un transport.
- Mise à jour des docstrings module dans `manager.py`, `agent.py`, `helpers.py` qui parlent du bypass.
- Mémoire à mettre à jour (`MEMORY.md`) avec un memo "Codex approvals — bridge sync/async + monkey-patch SDK".

---

## 5. Risques et gotchas

### 5.1 Deadlock — analyse fine

Le pattern `run_coroutine_threadsafe(...).result()` bloque la worker thread `asyncio.to_thread`. Tant qu'on attend la réponse user, **le `_transport_lock` est tenu** : pendant `_call_sync`, `async with self._transport_lock:` puis `asyncio.to_thread(...)` ne libère le lock qu'au retour du sync call.

**Conséquence directe** : pendant qu'une approval est in-flight :
- ❌ `await codex._client.close()` ⇒ deadlock (le close() prend lui aussi le lock).
- ❌ `await codex._client.turn_interrupt(thread_id, turn_id)` ⇒ deadlock (pareil — `AsyncAppServerClient` n'a pas de `turn_handle.interrupt()`, c'est une méthode du `_client` qui prend `thread_id, turn_id`).
- ❌ Toute autre méthode du SDK ⇒ deadlock.

**Solutions superposées** :

1. **Pour kill propre** : résoudre les pending d'abord (voir §2.4 ci-dessus). Le worker thread débloque → libère le lock → close() acquiert → tout va bien. C'est le chemin par défaut.

2. **Pour kill brutal (timeout du chemin propre)** : tuer le subprocess directement via `codex._client._sync._proc.kill()` — bypass complet du lock, le worker thread reçoit une exception sur son prochain `_read_message`, le `fut.result()` re-raise, on retombe sur `default_response_for`.

3. **Pour user-initiated `Cancel turn` button** (≠ kill agent) : envoyer la décision `"cancel"` via `resolve_pending_request` → l'approval handler renvoie `{"decision": "cancel"}` → Codex abort le turn → tout débloque naturellement. Pas de problème de lock parce qu'on a un user click qui résout la pending.

À tester soigneusement avec :
- Approval in-flight + user clique Stop (manager.kill_session) → doit unwind sans timeout dans les 5s.
- Approval in-flight + backend restart → subprocess.kill, pas de zombie.
- Deux approvals successives dans le même turn (chaque resolved → next arrives) → ok.

### 5.2 Race sur le frontend

Si l'utilisateur clique "Approve" pile au moment où l'agent meurt :
- Le backend reçoit le WS response.
- Le backend cherche le pending_request → introuvable → log warning, no-op.
- L'UI a déjà disparu (broadcast DEAD avant).

OK pas grave, c'est juste un log à ne pas paniquer.

### 5.3 Race sur le sandbox

Le SDK `monkey-patch` du `_approval_handler` doit être fait AVANT que le premier `thread_start` ne soit appelé, sinon des approvals reçus pendant les premières millisecondes auront le default handler (auto-accept). Le `_codex._client._sync` est construit dans le `__init__` de `AsyncCodex` ; il faut donc patcher juste après instanciation, avant tout `await thread_start`.

Tested in §4.2.3 : on patche dans `CodexAgent.__init__` qui est appelé avant le premier `thread_start`. OK.

### 5.4 Re-load et state persistence

`PendingRequest` est in-memory uniquement. Si le backend redémarre, on perd les pending — l'agent est de toute façon mort. À l'identique de Claude.

Si le frontend reload : le `active_processes` envoyé au reconnect inclut `pending_requests` (déjà testé pour Claude, `serialize_agent_info`). Donc reload OK.

### 5.5 Multi-approvals dans un même turn — sérialisées par le SDK

Le SDK Codex est **single-threaded par transport** : `_call_sync` prend `_transport_lock`, et `next_notification` consomme une seule notification à la fois. Donc deux approval requests pour le même turn arrivent **séquentiellement** : Codex émet la 1ère, attend notre réponse, puis seulement après émet la 2ème.

Conséquence : le scénario "N pending requests en parallèle" qu'on a chez Claude (parallel Read + Glob) **n'arrive pas naturellement** côté Codex pour un même turn. À chaque instant, il y a 0 ou 1 PendingRequest pour une session Codex.

Le code doit néanmoins gérer un dict de pending requests (≥ 0 entries) par symétrie avec Claude et pour rester robuste si une future version du SDK change ce comportement. Le multi-onglets ci-dessous reste valable indépendamment.

### 5.6 Multi-onglets

Si deux onglets ouverts sur la même session : les deux reçoivent le `process_state` avec le `pending_requests`. Les deux peuvent cliquer Approve. Le premier qui clique gagne (resolve_pending_request renvoie True), le second se voit retourné False, log warning. UI : l'onglet 2 verra la pending disparaître via le state broadcast suivant. Acceptable.

### 5.7 acceptForSession — pas de visibilité côté TwiCC

`acceptForSession` est stocké côté Codex en RAM (in-process cache). Donc :
- Si l'agent meurt et reboot (resume) : le cache est perdu, le user re-prompt.
- Si on a deux sessions Codex actives en parallèle : leurs caches sont indépendants.

Acceptable mais à mentionner dans la doc UI ("for this session" est littéralement "while this Codex process is alive").

### 5.8 `acceptWithExecpolicyAmendment` écrit en dur sur disque

Cette décision **modifie le fichier `~/.codex/rules/default.rules`** côté utilisateur. Conséquences :
- TwiCC ne contrôle pas ce qui est écrit (c'est le serveur Codex qui le fait).
- Si le user a un setup Codex pré-existant, on touche son fichier. **À mentionner clairement dans l'UI** : « This will permanently allow this command pattern across all your Codex sessions ».
- Préfixes bannis (`bash`, `python3`, `git`, etc.) : le serveur ne propose pas d'amendment dans `proposedExecpolicyAmendment`, donc on n'affichera pas le bouton "Add to allow list" pour ces commandes — pas de logique côté UI à ajouter, l'absence du champ suffit.

### 5.9 La 3ème approval (permissions) est rare mais bloquante

Si on l'oublie et qu'elle arrive en prod, le SDK reste bloqué pour toujours. Donc même si on ne lui fait pas une UI riche, il faut **au moins** un fallback "decline" qui se déclenche par défaut.

---

## 6. Mapping factorisation/non-factorisation final

| Composant | Aujourd'hui | Cible | Action |
|----------|-------------|-------|--------|
| `PendingRequest` dataclass | `twicc/agent/states.py` (neutral) | Inchangé | Aucune |
| `AgentInfo.pending_requests` | `twicc/agent/states.py` (neutral) | Inchangé | Aucune |
| `_pending_requests` dict | CC-specific | `BaseAgent` | Remonter |
| `_pending_futures` dict | CC-specific (typed) | `BaseAgent` (typé `Any`) | Remonter |
| `pending_requests` property | CC-specific | `BaseAgent` | Remonter |
| `get_info()` override | CC-specific | `BaseAgent` | Remonter (les fields neutres) |
| `_await_pending_request` helper | n'existe pas | `BaseAgent` | Créer |
| `_cancel_all_pending_futures` | `_cancel_pending_request_future` CC-specific | `BaseAgent` (sans paramètre — `future.cancel()` partout) | Refactor |
| `resolve_pending_request` agent | CC-specific | `BaseAgent` | Remonter |
| `resolve_pending_request` manager | CC-specific | `BaseAgentManager` | Remonter |
| Timeout monitor skip | CC-specific | `BaseAgentManager` | Remonter |
| `_handle_pending_request` (Claude) | CC-specific (SDK callback) | Reste CC-specific, devient un thin wrapper | Refactor |
| Codex `_sync_approval_handler` + `_async_approval_handler` | n'existent pas | Codex-specific | Créer |
| `permission_suggestions` flow | CC-specific | Inchangé | Aucune |
| `permission_mode` field | CC-specific (Session column) | Inchangé | Aucune (Codex n'utilise pas) |
| `setMode` persist branch (`ws.py:254-265`) | CC-specific | Inchangé | Aucune |
| WS message `claude_code:pending_request_response` | CC-specific (prefix) | Inchangé | Aucune |
| WS message `codex:pending_request_response` | n'existe pas | Codex-specific (prefix) | Créer |
| `respondToPendingRequest` JS | CC-specific (`providers/claude_code/ws.js`) | + Codex equivalent + central dispatch | Créer + refactor caller |
| `PendingRequestForm.vue` parent | Couplé CC | Provider-agnostique (dispatcher) | Refactor |
| `PendingRequestBody` sous-composants | n'existent pas | Un par provider | Extraire + créer |
| Pinia store `getPendingRequests` | Neutral | Inchangé | Aucune |

---

## 7. Questions à trancher (numérotées pour réponses faciles)

> Réponds-moi par `Q1: ...`, `Q2: ...` etc. dans ta prochaine message.

### **Q1 — Que fait-on du `permission_mode` Codex aujourd'hui dans le frontend ? — RÉPONSE TROUVÉE**

**Décision actée par toi** (cf. extrait de conversation antérieure) : on **garde** le sélecteur Codex et on le **mappe** côté backend vers `(sandbox_mode, approval_policy)`. Les 4 modes existants (`read_only`/`auto`/`autonomous`/`yolo`) restent + on **ajoute `strict`** comme 5ème mode pour rapprocher l'expérience du `don't ask` de Claude (refuse silencieusement sauf allowlist).

Quand tu m'avais dit "il n'y a pas de mode", tu voulais dire "rien n'est pluggué dans le backend, tout est force-autorisé en attendant" — pas qu'il fallait retirer le sélecteur.

Mapping détaillé dans **§4 Étape 7** (table + code Python).

Conséquences sur les autres questions : **Q7 et Q8 deviennent automatiquement résolues** par ce choix (voir leur encadré).

### **Q2 — Comment on branche le sync approval handler ? — RÉSOLUE**

**Décision : A** — monkey-patch `codex._client._sync._approval_handler` après instanciation.

Action obligatoire associée : ajouter à la mémoire `reference_codex_sdk_update_procedure.md` un check explicite à exécuter à chaque update du SDK vendored, pour vérifier que le chemin `_client._sync._approval_handler` est toujours accessible et que la signature `Callable[[str, JsonObject | None], JsonObject]` n'a pas bougé. **Fait dans cette même session** (cf. mémoire dédiée).

### **Q3 — Quelles décisions on expose dans l'UI ? — RÉSOLUE**

**Décision : 3 boutons visibles, avec un menu déroulant sur Approve.**

UI pour `commandExecution` :

- **Approve ▾** — split-button qui ouvre un menu :
  - `Once` → `accept`
  - `For session` → `acceptForSession`
  - `+ add allow rule` → `acceptWithExecpolicyAmendment` ou `applyNetworkPolicyAmendment`, **visible seulement si** le payload contient `proposedExecpolicyAmendment` ou `proposedNetworkPolicyAmendments` non-null. Codex ne propose pas pour les préfixes risqués (`bash`, `git`, `python3`, `sudo`, etc.), on ne filtre rien côté nous.
- **Deny** → `decline` (l'agent peut tenter autre chose)
- **Cancel turn** → `cancel` (abort propre du turn, l'agent reste en `user_turn`)

UI pour `fileChange` : même structure, mais le menu n'a que `Once` et `For session` (pas d'amendment pour les fichiers).

Distinction `decline` / `cancel` à expliciter dans les tooltips :
- `decline` = "Refuse cette action ; Codex peut tenter une autre approche."
- `cancel` = "Termine le turn ; Codex te rendra la main." (≠ Stop qui kill l'agent)

### **Q4 — On enrichit `PendingRequest` ou on stocke la "method" dans `tool_input` ? — RÉSOLUE PAR REFACTOR**

**Devenue obsolète.** En adoptant `future.cancel()` pour le kill (identique à Claude), la default response n'est plus choisie dans `interrupt_or_kill` mais dans le wrapper sync handler — qui a déjà `method` en argument. Donc pas besoin de stocker la method sur la `PendingRequest`.

Mécanisme :
```python
def _sync_approval_handler(self, method, params):
    try:
        return asyncio.run_coroutine_threadsafe(
            self._async_approval_handler(method, params), self._loop,
        ).result()
    except asyncio.CancelledError:
        return default_response_for(method)  # method connu dans le scope local
```

Et `interrupt_or_kill` fait juste `self._cancel_all_pending_futures()` (= `future.cancel()` partout, comme Claude). Plus de divergence entre les deux providers à ce niveau.

### **Q5 — Quoi faire de `item/permissions/requestApproval` ? — RÉSOLUE**

**Décision : même UI que les autres types, scope dans le menu Approve.**

| Boutons | Menu Approve |
|---------|--------------|
| Approve ▾ / Deny / Cancel turn | **For this turn** → `scope="turn"` / **For this session** → `scope="session"` |

`Approve` accorde **tout** ce que Codex a demandé (pas de granular sub-set). Si un jour la fréquence d'usage justifie une UI plus fine (cocher chaque permission individuellement), on évoluera. Pour l'instant la symétrie totale avec `commandExecution`/`fileChange` rend l'implémentation minimale.

Affichage : on liste les permissions demandées (network y/n, fileSystem entries) + la raison du modèle, en lecture seule. Bouton "+ add allow rule" du menu Approve absent ici (pas d'amendment pour les permissions).

### **Q6 — Affichage de la pending request dans la session vs en bottom-banner ? — RÉSOLUE**

**Décision : A — bottom-banner** (= cohérent avec Claude).

Détail pratique : pour `fileChange`, le diff arrive **avant** l'approval dans le stream (event `item/started` de l'`ApplyPatch`). Il est donc déjà rendu dans la liste comme un item normal au moment où la banner apparaît. La banner affichera juste un résumé léger (`"Modify N files"` ou similaire) + les 3 boutons. Le user peut scroller pour voir le diff complet rendu plus haut.

Pour `commandExecution` : la banner affiche la commande (1 ligne ellipsée) + cwd + reason si présente, puis les 3 boutons.

Pour `permissions` : la banner affiche la liste des permissions demandées + reason, puis les 3 boutons (avec le menu Approve → For this turn / For this session).

### **Q7 — Sandbox par défaut ? — RÉSOLUE PAR Q1**

Le défaut est le mode `auto` du préset : `sandbox = workspace-write` + `approval_policy = on-request`. C'est `DEFAULT_MODE = "auto"` dans `_PRESET_MAP` (§4 Étape 7).

### **Q8 — Faut-il un mode "auto-approve tout" pour des sessions de test rapides ? — RÉSOLUE PAR Q1**

C'est le mode `yolo` du préset : `sandbox = danger-full-access` + `approval_policy = never`. Toujours dispo via le sélecteur, donc rien à faire de spécial.

### **Q9 — `item/tool/call` et `account/chatgptAuthTokens/refresh` ? — RÉSOLUE**

**Décision : B — délégation passive + log warning** pour toutes les méthodes server qu'on ne gère pas, avec une liste explicite des chantiers à reprendre plus tard.

`_sync_approval_handler` : pour toute méthode qui n'est pas dans `APPROVAL_METHODS`, on log `WARNING: unhandled Codex server request: <method>` et on délègue au `_sdk_default_approval_handler` capturé en `__init__` (qui retourne `{}` ou la default-accept pour les 2 méthodes que LE SDK connaît).

**Liste des méthodes à supporter en v2/v3** (à ne pas oublier — voir §1.6 pour les details schema) :

| Méthode | Quand on s'y attaque |
|---------|----------------------|
| `item/tool/call` | Quand TwiCC supportera les MCP servers — implémenter le dispatch MCP |
| `item/tool/requestUserInput` | Quand on voudra l'équivalent du `ask_user_question` Claude pour Codex |
| `mcpServer/elicitation/request` | Quand TwiCC supportera les MCP servers — formulaires interactifs |
| `account/chatgptAuthTokens/refresh` | Rien à faire en principe — TwiCC anticipe via `credentials.py`. Si log warning observé en prod → investiguer |

### **Q10 — D'où vient le diff pour l'approval `fileChange` côté UI ? — RÉSOLUE**

**Décision : A — side-table backend `_items_by_id`.**

Précisions importantes :
- **B n'est pas viable** : le store Pinia frontend reçoit des `SessionItem` issus du JSONL (via watcher), **pas** les events stream du SDK Codex. Donc le frontend n'a pas le payload du `item/started` natif disponible.
- `_items_by_id: dict[str, dict]` sur `CodexAgent`, indexé par `itemId`. Update sur `item/started`, pop sur `item/completed`, clear complet sur `interrupt_or_kill`.
- Au moment de créer la `PendingRequest` pour `fileChange`, on injecte le payload connu dans `tool_input["_item_payload"]` (contient `changes: [{diff, kind, path}]`).
- L'UI banner affiche la **liste des paths + leur `kind`** (add/update/delete), pas le diff complet. Le diff est déjà rendu plus haut dans la liste comme un item normal `ApplyPatch`.

**Modèle multi-fichiers** : un `apply_patch` = un seul `fileChange/requestApproval`, qui peut toucher N fichiers (array `changes: […]`). L'approval est **tout-ou-rien** : `accept` applique tout le patch, `decline` rejette tout. Pas d'approval partielle, comme `MultiEdit` côté Claude. Si le modèle veut des approvals indépendantes par fichier, il fait N appels séparés (sérialisés par le SDK, cf. §5.5).

### **Q11 — Validation de la réponse WS côté backend ? — RÉSOLUE**

**Décision : A — validation stricte côté backend.**

Dans `_build_codex_response` (cf. §4 Étape 3), valider :
- `decision` est soit un string dans la whitelist (`accept` / `acceptForSession` / `decline` / `cancel`), soit un dict avec exactement une clé `acceptWithExecpolicyAmendment` ou `applyNetworkPolicyAmendment` (chacune avec le sous-objet attendu).
- Pour `permissions` : `scope ∈ {"turn", "session"}`, `permissions` est un dict (peut être vide).
- Pour `fileChange` : whitelist plus restreinte (pas de variants amendment).

Si invalide → `logger.error("invalid decision payload from frontend: %r", content)` + envoyer un default safe (`{"decision": "decline"}` ou `{"permissions": {}, "scope": "turn"}` pour permissions) au SDK pour ne pas le laisser bloqué.

### **Q12 — Confirmer qu'on utilise AsyncCodex uniquement ? — RÉPONSE TROUVÉE**

**Vérifié** : `manager.py:219` fait `codex = AsyncCodex(config=config)`. Donc le path de monkey-patch `codex._client._sync._approval_handler` est correct. Aucune ambiguïté. Pas de question à trancher ici — c'est juste à documenter dans le commentaire du monkey-patch.

### **Q13 — Ordre de bataille ? — RÉSOLUE**

**Critère retenu** : chaque PR doit être mergeable seule sans rien casser. Si on stoppe ou switche après n'importe laquelle, l'app reste cohérente.

**Décision : 5 PR.**

#### PR1 — Refactor `BaseAgent` pour partager la plomberie pending requests
- Remonte les dicts (`_pending_requests`, `_pending_futures`), property, helpers (`_await_pending_request`, `_cancel_all_pending_futures`, `resolve_pending_request`), override `get_info()` de `ClaudeCodeAgent` vers `BaseAgent`.
- Remonte aussi le `resolve_pending_request` côté `BaseAgentManager` et le skip timeout-monitor.
- Pure refactor. Claude inchangé, Codex toujours en bypass.
- ✅ Mergeable seule.

#### PR2a — Backend Codex approvals + permission modes câblés (bypass conservé)
- `src/twicc/providers/codex/agent/approvals.py` : tout le module.
- `CodexAgent` : monkey-patch handler + `_sync_approval_handler` + `_async_approval_handler` + override `_handle_stream_event` pour `_items_by_id` (side-table diff).
- WS handler `_handle_pending_request_response` + `_build_codex_response` (validation stricte).
- `src/twicc/providers/codex/permission_modes.py` : mapping 4 modes existants → (SandboxMode, AskForApproval).
- `CodexAgentManager._create_agent` : appelle `resolve_codex_policy(settings.permission_mode)` MAIS **default reste `yolo`** côté session.permission_mode si null (= bypass équivalent).
- Tout le code est en place. Aucun changement de comportement user-visible (sessions Codex tournent toujours en yolo par défaut).
- ✅ Mergeable seule.

#### PR2b — Lève le bypass + frontend stub minimal
- Backend : change le `DEFAULT_MODE` de `permission_modes.py` à `"auto"`. Sessions sans `permission_mode` explicite passent en `workspace-write + on-request`.
- Frontend : 3 boutons simples (Approve / Deny / Cancel turn) sans menus déroulants, sans rendu riche. Dispatcher `respondToPendingRequest` provider-agnostique + `respondToPendingRequest` Codex dans `codex/ws.js`. Sous-composant `PendingRequestBody` Codex stub (affiche commande/paths/permissions en texte plat).
- `PendingRequestForm.vue` découplé de l'import Claude dur.
- ✅ Mergeable seule. Première feature user-visible : on peut approuver/refuser une commande Codex depuis l'UI.

#### PR3 — Frontend riche + 5ème mode `strict`
- Menus déroulants split-button sur Approve (Once / For session / +add allow rule conditionnel pour commandExecution ; Once / For session pour fileChange ; For this turn / For this session pour permissions).
- Sous-composants `PendingRequestBody` Codex spécialisés par `tool_name` (rendu propre des commandes, paths, diff référence, permissions).
- Tooltips clairs pour Deny vs Cancel turn.
- Ajout `STRICT: 'strict'` à `PERMISSION_MODE` (frontend constants.js + helpers.js).
- Ajout `"strict"` dans `_PRESET_MAP` côté backend (`(read-only, never)`).
- ✅ Mergeable seule.

#### PR4 — Tests + docs + mémoires
- Tests unitaires : `_build_codex_response`, `make_pending_request`, `default_response_for`, `resolve_codex_policy`, `derive_request_id`.
- Mémoires : nouveau `project_codex_approvals.md` (architecture bridge sync/async + monkey-patch).
- Mise à jour CHANGELOG.md.
- ✅ Mergeable seule.

### Acceptance criteria par PR

- **PR1** : Claude sessions continuent de marcher exactement comme avant (approval flow CC inchangé end-to-end). `BaseAgent` expose les dicts/property/helpers de pending requests, utilisables par n'importe quel provider. Aucune session Codex impactée (bypass toujours en place).
- **PR2a** : Tout le code Codex approval est compilable et chargeable. Une session Codex démarrée sans `permission_mode` explicite tourne TOUJOURS en mode `yolo` (= `danger-full-access` + `never`) — comportement strictement identique à avant. Aucune approval UI ne se déclenche. Le `_PRESET_MAP` existe avec 4 entrées (`read_only`/`auto`/`autonomous`/`yolo`). Le monkey-patch handler est installé. WS handler + validation présents mais jamais sollicités.
- **PR2b** : Une session Codex fraîche démarrée sans `permission_mode` explicite tombe sur `DEFAULT_MODE = "auto"` (= `workspace-write` + `on-request`). À la première commande shell qui sort des opérations triviales, le frontend affiche une PendingRequest avec 3 boutons (Approve / Deny / Cancel turn). L'user peut cliquer et la session reprend. Pareil pour `fileChange` et `permissions`. Sessions Claude inchangées.
- **PR3** : Le bouton Approve devient un split-button avec menu déroulant. `commandExecution` Approve menu : Once / For session / +add allow rule (conditionnel). `fileChange` Approve menu : Once / For session. `permissions` Approve menu : For this turn / For this session. Le rendu spécifique par type est en place. Le 5ème mode `strict` apparaît dans le picker frontend et fonctionne côté backend.
- **PR4** : Tests unitaires verts pour `_build_codex_response`, `make_pending_request`, `default_response_for`, `resolve_codex_policy`, `derive_request_id`. Mémoire `project_codex_approvals.md` existe. CHANGELOG.md mentionne la feature dans `[Unreleased]`.

---

## 8. Checklist de coding (pour Daisy)

Une fois les Q tranchées, voici la checklist consolidée. À cocher pendant l'implémentation.

### Backend

- [ ] `BaseAgent` : ajouter `_pending_requests`, `_pending_futures`, `pending_requests` property, `_await_pending_request`, `_cancel_all_pending_futures()` (sans paramètre — `future.cancel()`), `resolve_pending_request`.
- [ ] `BaseAgent.get_info()` : injecter `pending_requests` dans le snapshot par défaut.
- [ ] `BaseAgentManager.resolve_pending_request(session_id, request_id, response) -> bool`.
- [ ] `BaseAgentManager` : timeout monitor skip si `agent.pending_requests`.
- [ ] `ClaudeCodeAgent` : remplacer dicts/property locaux par ceux de la base, `_handle_pending_request` devient un thin wrapper qui :
  - construit le `PendingRequest`
  - appelle `_await_pending_request`
  - cast en `PermissionResultAllow/Deny`
- [ ] `ClaudeCodeAgentManager` : retirer la copie de `resolve_pending_request`.
- [ ] `ClaudeCodeAgent` : `_cancel_pending_request_future` → utiliser `_cancel_all_pending_futures()` héritée de BaseAgent (sémantique inchangée : `future.cancel()`).
- [ ] **Nouveau** `src/twicc/providers/codex/agent/approvals.py` : `APPROVAL_METHODS`, `is_approval_method`, `derive_request_id`, `make_pending_request`, `default_response_for` (sans `METHOD_FROM_TOOL_NAME`, plus utile).
- [ ] `CodexAgent.__init__` : monkey-patch `self._codex._client._sync._approval_handler = self._sync_approval_handler` (juste après `super().__init__()` et avant le retour).
- [ ] `CodexAgent._sync_approval_handler` : bridge synchrone qui appelle `_async_approval_handler` via `run_coroutine_threadsafe`, attrape `asyncio.CancelledError` → renvoie `default_response_for(method)`.
- [ ] `CodexAgent._async_approval_handler` : crée PendingRequest, await, return response dict.
- [ ] `CodexAgent.interrupt_or_kill` : avant de fermer le transport, faire `self._cancel_all_pending_futures()`. Le sync handler convertit ensuite les CancelledError en réponses safe au SDK.
- [ ] **Nouveau** `src/twicc/providers/codex/permission_modes.py` : `_PRESET_MAP` (5 modes → SandboxMode+AskForApproval), `DEFAULT_MODE="auto"`, `resolve_codex_policy(mode)`.
- [ ] `CodexAgentManager._create_agent` : lever le bypass — appeler `resolve_codex_policy(settings.permission_mode)` et passer le couple à `thread_start`/`thread_resume`.
- [ ] `src/twicc/providers/codex/agent/manager.py` : retirer la docstring sur le bypass.
- [ ] **Nouveau** `src/twicc/providers/codex/ws.py` : `CodexWSHandler._handle_pending_request_response` qui valide, construit `response` dict via `_build_codex_response`, dispatch au manager.
- [ ] Codex WS message type : `codex:pending_request_response`.
- [ ] `credentials.py` et `title_suggest.py` : conservent leur bypass local (commenter pour clarifier qu'il est intentionnel).

### Frontend

- [ ] `frontend/src/providers/index.js` : `respondToPendingRequest(provider, sessionId, requestId, payload)` dispatcher.
- [ ] **Nouveau** `frontend/src/providers/codex/ws.js` : `respondToPendingRequest` qui envoie `codex:pending_request_response`.
- [ ] `frontend/src/components/message/PendingRequestForm.vue` : remplacer l'import dur par `respondToPendingRequest` du dispatcher ; ajouter le routage par provider pour le body.
- [ ] **Nouveau** `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` : rendu par tool_name (`commandExecution` / `fileChange` / `permissions`) + boutons selon Q3.
- [ ] Refactor (peut-être) `PendingRequestBody.vue` claude_code en composant à part en symétrie.
- [ ] Stylings cohérents avec ce qui existe pour Claude.
- [ ] `frontend/src/providers/codex/constants.js` : ajouter `STRICT: 'strict'` à `PERMISSION_MODE` (5ème mode).
- [ ] `frontend/src/providers/codex/helpers.js` : ajouter l'entrée `strict` dans `AGENT_SETTINGS_CHOICES` avec label + help text (cf. mapping §4 Étape 7).
- [ ] Vérifier que la sidebar / picker affiche les 5 modes et que la sélection se sauve correctement dans `Session.permission_mode`.

### Tests

- [ ] `_build_codex_response` : matrice de tests décisions → wire payload.
- [ ] `make_pending_request` : 3 methods → bonnes shapes.
- [ ] `default_response_for` : 3 methods → safe defaults.
- [ ] `resolve_codex_policy` : 5 modes + unknown + None → bons couples (SandboxMode, AskForApproval) ; vérifier fallback sur `auto`.
- [ ] (Optionnel) Test d'intégration : agent reçoit une requête approval mockée, broadcast, frontend WS payload validate.

### Docs / mémoire

- [ ] Mémoire `project_codex_approvals.md` : pattern du bridge sync/async + monkey-patch privé.
- [ ] `reference_sdk_update_procedure.md` : ajouter "vérifier que `_client._sync._approval_handler` est encore accessible après update" comme étape.
- [ ] Mettre à jour le docstring `manager.py:1-9`, `agent.py:7-9` (CodexAgent).
- [ ] CHANGELOG.md : entrée pour la feature (selon convention).

---

## 9. Annexe — exemple de message wire complet

### 9.1 Stream Codex → Backend (server request)

```json
{
    "id": "req-42",
    "method": "item/commandExecution/requestApproval",
    "params": {
        "threadId": "019e1d24-18ec-7ee3-bbda-7ebe2752123d",
        "turnId": "019e1d24-1934-7ef3-a168-1e2d62602ac8",
        "itemId": "call_xyz",
        "startedAtMs": 1712345678901,
        "command": "/bin/bash -lc \"rm -rf /tmp/build\"",
        "cwd": "/home/twidi/dev/twicc-poc",
        "commandActions": [{"command": "rm -rf /tmp/build", "type": "unknown"}],
        "reason": "cleaning up build cache",
        "proposedExecpolicyAmendment": null,
        "proposedNetworkPolicyAmendments": null,
        "networkApprovalContext": null
    }
}
```

### 9.2 Backend → Frontend (WS process_state)

```json
{
    "type": "process_state",
    "session_id": "019e1d24-...",
    "state": "assistant_turn",
    "pending_requests": [
        {
            "request_id": "call_xyz",
            "request_type": "tool_approval",
            "tool_name": "commandExecution",
            "tool_input": { /* params verbatim */ },
            "created_at": 1712345678.901
        }
    ]
}
```

### 9.3 Frontend → Backend (WS pending_request_response)

```json
{
    "type": "codex:pending_request_response",
    "session_id": "019e1d24-...",
    "request_id": "call_xyz",
    "tool_name": "commandExecution",
    "decision": "decline"
}
```

### 9.4 Backend → Codex (server response)

```json
{
    "id": "req-42",
    "result": {
        "decision": "decline"
    }
}
```

### 9.5 Exemple avec amendment

Frontend envoie :
```json
{
    "type": "codex:pending_request_response",
    "session_id": "...",
    "request_id": "...",
    "tool_name": "commandExecution",
    "decision": {
        "acceptWithExecpolicyAmendment": {
            "execpolicy_amendment": ["rm", "-rf"]
        }
    }
}
```

Backend wrappe verbatim dans `{"decision": {...}}` et envoie à Codex.

---

## 10. Fin

Si tu veux affiner ce doc avant qu'on attaque l'implémentation, n'hésite pas. Sinon : réponses aux Q ⇒ on peut commencer par l'étape 1 (refactor BaseAgent) dès demain.
