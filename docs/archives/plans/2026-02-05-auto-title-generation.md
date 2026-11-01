# Plan : Auto-génération de titres avec Claude Haiku

## Résumé

Générer automatiquement des suggestions de titre via Claude Haiku. Trois modes de déclenchement :

1. **Draft** : L'utilisateur ouvre la modale de renommage sur une session draft → on utilise le texte du textarea
2. **New Session** : Création d'une nouvelle session → on utilise le prompt envoyé pour créer la session
3. **Existing Session** : L'utilisateur ouvre la modale sur une session existante → on récupère le premier message en base

Dans les 3 cas :
- Le frontend envoie `{ type: "suggest_title", sessionId, prompt? }` (le `sessionId` est **toujours** envoyé pour le retrouver dans la réponse)
- La réponse arrive via WebSocket (`title_suggested`) avec le `sessionId`
- La suggestion est stockée dans le store : `titleSuggestions[sessionId] = { suggestion, sourcePrompt? }`
- Si une suggestion existe déjà dans le store, on ne refait pas de demande (sauf pour draft si le message a changé)

## Architecture

```
                                    ┌─────────────────────────────┐
   Frontend                         │         Backend             │
                                    │                             │
┌──────────────────┐                │  ┌───────────────────────┐  │
│ SessionRename    │                │  │ WebSocket Consumer    │  │
│ Dialog.vue       │───WSS──────────┼─▶│ handle_suggest_title()│  │
│                  │  suggest_title │  └───────────┬───────────┘  │
│ • draft+prompt   │  {sessionId,   │              │              │
│ • new/existing   │   prompt?}     │              ▼              │
└──────────────────┘                │  ┌───────────────────────┐  │
        ▲                           │  │ title_suggest.py      │  │
        │                           │  │ generate_title()      │  │
        │                           │  │                       │  │
        │                           │  │ • Claude SDK + Haiku  │  │
        │                           │  │ • permission-mode     │  │
        │                           │  │ • no-session-persist  │  │
        │                           │  └───────────┬───────────┘  │
        │                           │              │              │
        │           ◀───WSS─────────┼──────────────┘              │
        │           title_suggested │                             │
        │           {sessionId,     │                             │
        │            suggestion,    │                             │
        │            sourcePrompt?} └─────────────────────────────┘
        │
        └─── Store: titleSuggestions[sessionId] = { suggestion, sourcePrompt? }
```

## Fichiers à modifier/créer

| Fichier | Action |
|---------|--------|
| `src/twicc/title_suggest.py` | **CRÉER** - Service de génération via SDK Haiku |
| `src/twicc/asgi.py` | Ajouter handler `suggest_title` + broadcast `title_suggested` |
| `frontend/src/stores/data.js` | Ajouter `titleSuggestions` reactive map + handler WS |
| `frontend/src/components/SessionRenameDialog.vue` | UI suggestion depuis store + trigger pour draft/new/existing |

## Implémentation détaillée

### 1. Backend : `src/twicc/title_suggest.py` (nouveau)

```python
"""
Title suggestion service using Claude Haiku via the Agent SDK.
"""
import asyncio
import logging
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

from twicc.compute import extract_text_from_content, get_message_content
from twicc.core.models import SessionItem
from twicc.core.enums import ItemKind
import orjson

logger = logging.getLogger(__name__)

SUGGESTION_TIMEOUT_SECONDS = 15

TITLE_PROMPT = """Summarize the following user message in 5-7 words to create a concise session title.
Return ONLY the title, nothing else. No quotes, no explanation, no punctuation at the end.

IMPORTANT: The title must be in the same language as the user message. However, do not translate technical terms or words that are already in another language (e.g., if the user writes in French about code, keep English technical terms as-is).

User message:
{message}"""


async def generate_title_from_prompt(prompt: str) -> str | None:
    """
    Generate a title suggestion from a prompt text.
    Used for draft sessions and new sessions.
    """
    return await _call_haiku(prompt)


async def generate_title_from_session(session_id: str) -> str | None:
    """
    Generate a title suggestion for an existing session.
    Fetches the first user message from the database.
    """
    first_message = await _get_first_user_message(session_id)
    if not first_message:
        logger.debug("No user message found for session %s", session_id)
        return None

    return await _call_haiku(first_message)


async def _get_first_user_message(session_id: str) -> str | None:
    """Extract text from the first user message in a session."""
    from asgiref.sync import sync_to_async

    @sync_to_async
    def fetch():
        item = SessionItem.objects.filter(
            session_id=session_id,
            kind=ItemKind.USER_MESSAGE
        ).order_by('line_num').first()

        if not item:
            return None

        try:
            parsed = orjson.loads(item.content)
            content = get_message_content(parsed)
            return extract_text_from_content(content)
        except Exception as e:
            logger.warning("Failed to parse message for session %s: %s", session_id, e)
            return None

    return await fetch()


async def _call_haiku(user_message: str) -> str | None:
    """
    Call Claude Haiku via the SDK and return the title suggestion.

    Uses SDK in streaming mode, sends one message, waits for response, then kills.
    """
    # Truncate long messages
    if len(user_message) > 2000:
        user_message = user_message[:2000] + "..."

    full_prompt = TITLE_PROMPT.format(message=user_message)

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
    )

    client = ClaudeSDKClient(options=options)

    try:
        await asyncio.wait_for(
            client.connect(),
            timeout=SUGGESTION_TIMEOUT_SECONDS
        )

        await client.query(full_prompt)

        # Collect response
        response_text = ""
        async for msg in client.receive_messages():
            if msg.type == "assistant" and hasattr(msg, "content"):
                # Extract text from content
                for block in msg.content:
                    if hasattr(block, "text"):
                        response_text += block.text
            elif msg.type == "result":
                # End of response
                break

        suggestion = response_text.strip()

        # Basic validation
        if not suggestion or len(suggestion) > 200:
            return None

        return suggestion

    except asyncio.TimeoutError:
        logger.warning("Haiku title suggestion timed out")
        return None
    except Exception as e:
        logger.exception("Error calling Haiku for title suggestion: %s", e)
        return None
    finally:
        # Kill the process
        try:
            await client.close()
        except Exception:
            pass
```

### 2. Backend : `src/twicc/asgi.py` - Handler WebSocket

Ajouter dans le consumer :

```python
async def handle_suggest_title(self, data: dict) -> None:
    """
    Handle title suggestion request.

    Modes:
    - prompt provided: Use prompt directly (draft/new session)
    - session_id only: Fetch first message from DB (existing session)
    """
    from twicc.title_suggest import generate_title_from_prompt, generate_title_from_session

    session_id = data.get("sessionId")
    prompt = data.get("prompt")

    if not session_id:
        return

    # Generate suggestion
    if prompt:
        suggestion = await generate_title_from_prompt(prompt)
    else:
        suggestion = await generate_title_from_session(session_id)

    # Broadcast result (include sourcePrompt for draft invalidation)
    await self.send_json({
        "type": "title_suggested",
        "sessionId": session_id,
        "suggestion": suggestion,  # Can be None
        "sourcePrompt": prompt,    # Only for draft mode, None otherwise
    })
```

Ajouter dans le dispatch des messages :

```python
elif msg_type == "suggest_title":
    await self.handle_suggest_title(data)
```

### 3. Frontend : `src/stores/data.js`

Ajouter dans le state :

```javascript
// Title suggestions by session ID
// Format: { sessionId: { suggestion: string, sourcePrompt?: string } }
titleSuggestions: {},
```

Ajouter dans les actions :

```javascript
/**
 * Request a title suggestion via WebSocket.
 * @param {string} sessionId - The session ID
 * @param {string|null} prompt - Optional prompt text (for draft/new sessions)
 */
requestTitleSuggestion(sessionId, prompt = null) {
    const message = { type: 'suggest_title', sessionId }
    if (prompt) {
        message.prompt = prompt
    }
    this.sendWebSocket(message)
},

/**
 * Handle title_suggested message from WebSocket.
 */
handleTitleSuggested(data) {
    const { sessionId, suggestion, sourcePrompt } = data
    if (suggestion) {
        this.titleSuggestions[sessionId] = { suggestion, sourcePrompt }
    }
},

/**
 * Get stored title suggestion for a session.
 */
getTitleSuggestion(sessionId) {
    return this.titleSuggestions[sessionId]?.suggestion || null
},

/**
 * Get the source prompt used for a suggestion (for draft invalidation).
 */
getTitleSuggestionSourcePrompt(sessionId) {
    return this.titleSuggestions[sessionId]?.sourcePrompt || null
},

/**
 * Clear title suggestion for a session (after use).
 */
clearTitleSuggestion(sessionId) {
    delete this.titleSuggestions[sessionId]
},
```

Dans le handler WebSocket existant, ajouter :

```javascript
case 'title_suggested':
    this.handleTitleSuggested(data)
    break
```

### 4. Frontend : `SessionRenameDialog.vue`

**Nouvelle prop** pour recevoir le texte du draft :

```javascript
const props = defineProps({
    session: { type: Object, default: null },
    draftMessage: { type: String, default: '' },  // Texte actuel du textarea (pour drafts)
})
```

**Nouveaux refs** :

```javascript
const isLoadingSuggestion = ref(false)
```

**Computed pour la suggestion** :

```javascript
const suggestion = computed(() => {
    if (!props.session) return null
    return store.getTitleSuggestion(props.session.id)
})
```

**Modification de `open()`** - gère les 3 cas :

```javascript
function open({ showHint = false } = {}) {
    errorMessage.value = ''
    showContextHint.value = showHint
    syncFormState()

    if (dialogRef.value) {
        dialogRef.value.open = true
    }

    if (!props.session) return

    const sessionId = props.session.id
    const existingSuggestion = store.getTitleSuggestion(sessionId)

    if (props.session.draft) {
        // DRAFT: utiliser draftMessage, refaire si le message a changé
        const currentPrompt = props.draftMessage?.trim()
        const previousPrompt = store.getTitleSuggestionSourcePrompt(sessionId)

        if (!currentPrompt) return  // Pas de message, pas de suggestion

        if (!existingSuggestion || previousPrompt !== currentPrompt) {
            isLoadingSuggestion.value = true
            store.requestTitleSuggestion(sessionId, currentPrompt)
        }
    } else {
        // EXISTING ou NEW SESSION: utiliser le premier message en base
        if (!existingSuggestion) {
            isLoadingSuggestion.value = true
            store.requestTitleSuggestion(sessionId)
        }
    }
}
```

**Watcher pour recevoir la suggestion** :

```javascript
watch(
    () => props.session && store.getTitleSuggestion(props.session.id),
    (newSuggestion) => {
        if (newSuggestion && isLoadingSuggestion.value) {
            isLoadingSuggestion.value = false
        }
    }
)
```

**Fonction pour appliquer la suggestion** :

```javascript
function applySuggestion() {
    if (suggestion.value) {
        localTitle.value = suggestion.value
        if (titleInputRef.value) {
            titleInputRef.value.value = suggestion.value
        }
        // Clear from store after use
        if (props.session) {
            store.clearTitleSuggestion(props.session.id)
        }
    }
}
```

**Template** (après context-hint, avant form-group) :

```html
<!-- Title suggestion -->
<div class="suggestion-section">
    <div v-if="isLoadingSuggestion" class="suggestion-loading">
        <wa-spinner size="small"></wa-spinner>
        <span>Generating suggestion...</span>
    </div>
    <div v-else-if="suggestion" class="suggestion-available">
        <span class="suggestion-label">Suggestion:</span>
        <a href="#" class="suggestion-link" @click.prevent="applySuggestion">
            {{ suggestion }}
        </a>
    </div>
</div>
```

**Styles** :

```css
.suggestion-section {
    margin-bottom: var(--wa-space-m);
}

.suggestion-loading {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.suggestion-available {
    font-size: var(--wa-font-size-s);
}

.suggestion-label {
    color: var(--wa-color-text-quiet);
    margin-right: var(--wa-space-xs);
}

.suggestion-link {
    color: var(--wa-color-brand);
    text-decoration: none;
    cursor: pointer;
}

.suggestion-link:hover {
    text-decoration: underline;
}
```

## Flux complets

### Draft Session
1. User ouvre la modale de renommage sur un draft
2. `SessionRenameDialog` reçoit `draftMessage` (texte actuel du textarea)
3. → `suggest_title` envoyé via WS avec le prompt
4. Backend lance Haiku, renvoie `title_suggested` avec `sourcePrompt`
5. Store stocke `{ suggestion, sourcePrompt }`
6. Suggestion affichée dans la modale
7. Si l'user rouvre la modale plus tard avec un message différent → nouvelle demande

### New Session
1. User crée une nouvelle session avec un prompt
2. Une fois la session créée, l'user ouvre la modale rename
3. → `suggest_title` envoyé via WS avec `sessionId` seulement (message en DB)
4. Même flux que existing

### Existing Session
1. User ouvre la modale de renommage
2. → `suggest_title` envoyé via WS avec `sessionId` seulement
3. Backend récupère le premier message en DB, lance Haiku
4. `title_suggested` reçu → affiché dans la modale

## Gestion des erreurs

| Cas | Comportement |
|-----|--------------|
| Haiku timeout (>15s) | `suggestion: null` envoyé, rien affiché |
| Haiku échoue | `suggestion: null` envoyé, rien affiché |
| Pas de message user | `suggestion: null` envoyé, rien affiché |
| User ferme modale avant réponse | Suggestion stockée, affichée si modale réouverte |

## Vérification

1. **Existing session** : Ouvrir modale rename → spinner → suggestion apparaît → clic → appliqué
2. **New session** : Créer session → ouvrir modale → suggestion déjà là (ou en cours)
3. **Draft session** : Envoyer message draft → ouvrir modale → suggestion disponible
4. **Timeout** : Vérifier que ça ne bloque pas si Haiku ne répond pas
