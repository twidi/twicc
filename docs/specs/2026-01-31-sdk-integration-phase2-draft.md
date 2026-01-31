# SDK Integration - Phase 2 (Draft)

**Date:** 2026-01-31
**Status:** Draft - à implémenter après Phase 1
**Dépendance:** Phase 1 doit être terminée

---

## Overview

Cette spec couvre les fonctionnalités reportées de Phase 1 :
- Création de nouvelles sessions
- Kill de sessions depuis le frontend
- Timeouts automatiques
- État `starting`

---

## 1. Création de Nouvelles Sessions

### 1.1 Problème

L'utilisateur crée une session, mais on ne connaît pas le `session_id` tant que Claude n'a pas répondu. L'utilisateur peut naviguer ailleurs ou créer plusieurs sessions en parallèle.

### 1.2 Solution : Temporary ID

1. Frontend génère un ID temporaire (e.g., `temp-abc123`)
2. Frontend envoie : `{type: "new_session", temp_id: "temp-abc123", project_id: "...", text: "..."}`
3. Frontend crée une entrée "ghost" dans la liste des sessions avec `temp-abc123`
4. Backend garde le mapping : `temp-abc123 → process`
5. Backend reçoit le `session_id` de Claude via `SystemMessage(subtype="init")`
6. Backend envoie : `{type: "session_created", temp_id: "temp-abc123", session_id: "claude-conv-xxx"}`
7. Frontend remplace l'entrée ghost par la vraie session
8. Si l'utilisateur est sur la page de création pour `temp-abc123` → redirect vers `/session/claude-conv-xxx`

### 1.3 ProcessManager étendu

```python
class ProcessManager:
    _processes: dict[str, "ClaudeSession"]  # temp_id ou session_id → session
    _temp_to_session: dict[str, str]        # temp_id → session_id
    _session_to_temp: dict[str, str]        # session_id → temp_id

    async def create_session(
        self,
        temp_id: str,
        project_id: str,
        cwd: str,
        prompt: str
    ) -> None:
        """Create a new Claude session (not resume)."""
        ...

    async def kill_session(self, session_id: str) -> None:
        """Terminate a session from frontend request."""
        ...
```

### 1.4 État `starting`

Ajout d'un 4ème état pour les nouvelles sessions :

| State | Meaning | Transition trigger |
|-------|---------|-------------------|
| `starting` | Process lancé, attente du session_id | Process créé |
| `assistant_turn` | Claude travaille | Reçu `SystemMessage(subtype="init")` |
| `user_turn` | Attente message utilisateur | Reçu `ResultMessage` |
| `dead` | Process terminé | Error, kill, timeout, shutdown |

```
[create_session] → starting
                      ↓ (received init, got session_id)
                assistant_turn ←──────┐
                      ↓ (received result)  │
                  user_turn ───────────┘ (user sends message)
                      ↓ (timeout/kill/error)
                    dead
```

---

## 2. Kill Session depuis le Frontend

### 2.1 Message WebSocket

```json
// Frontend → Backend
{
    "type": "kill_session",
    "session_id": "claude-conv-xxx"
}

// Backend → Frontend
{
    "type": "session_killed",
    "session_id": "claude-conv-xxx",
    "reason": "manual"
}
```

### 2.2 UI

- Bouton "Stop" visible quand le process est en `starting` ou `assistant_turn`
- Confirmation optionnelle avant kill

---

## 3. Timeouts Automatiques

### 3.1 Seuils

- **Idle timeout:** 15 minutes après dernier message utilisateur (état `user_turn`)
- **Thinking timeout:** 1 heure en attente de réponse Claude (état `assistant_turn`)

### 3.2 Implémentation

Tâche asyncio périodique (toutes les 60 secondes) :

```python
async def _cleanup_loop(self):
    while True:
        await asyncio.sleep(60)
        await self.cleanup_stale()

async def cleanup_stale(self):
    now = time.time()
    to_kill = []

    for session_id, session in self._processes.items():
        idle_time = now - session.last_activity
        if session.state == "user_turn" and idle_time > 15 * 60:
            to_kill.append((session, "idle_timeout"))
        elif session.state == "assistant_turn" and idle_time > 60 * 60:
            to_kill.append((session, "thinking_timeout"))

    for session, reason in to_kill:
        await session.kill()
        # Notify frontend
        await self._broadcast({
            "type": "session_killed",
            "session_id": session.session_id,
            "reason": reason
        })
```

### 3.3 Message WebSocket

```json
{
    "type": "session_killed",
    "session_id": "claude-conv-xxx",
    "reason": "idle_timeout" | "thinking_timeout"
}
```

---

## 4. Messages WebSocket Complets (Phase 2)

### Frontend → Backend

```json
// Créer nouvelle session
{
    "type": "new_session",
    "temp_id": "temp-abc123",
    "project_id": "proj-xyz",
    "text": "Help me with..."
}

// Kill session
{
    "type": "kill_session",
    "session_id": "claude-conv-xxx"
}
```

### Backend → Frontend

```json
// Session créée (temp_id résolu en session_id)
{
    "type": "session_created",
    "temp_id": "temp-abc123",
    "session_id": "claude-conv-xxx",
    "project_id": "proj-xyz"
}

// Session killed
{
    "type": "session_killed",
    "session_id": "claude-conv-xxx",
    "reason": "manual" | "idle_timeout" | "thinking_timeout" | "error"
}
```

---

## 5. UI Phase 2

### 5.1 Création de session

- Bouton "Nouvelle conversation" dans la liste des sessions
- Formulaire avec textarea pour le premier message
- Entrée "ghost" avec spinner pendant `starting`
- Redirect automatique vers la nouvelle session une fois créée

### 5.2 Kill session

- Bouton "Stop" visible pendant `starting` ou `assistant_turn`
- Le bouton remplace temporairement "Envoyer"

### 5.3 États UI étendus

| État process | Textarea | Bouton principal | Bouton stop |
|--------------|----------|------------------|-------------|
| `starting` | disabled | disabled | visible |
| `assistant_turn` | disabled | disabled | visible |
| `user_turn` | enabled | "Envoyer" | hidden |
| `dead` ou aucun | enabled | "Envoyer" | hidden |

---

## 6. Dépendances Phase 1

Cette Phase 2 suppose que Phase 1 est terminée :
- ProcessManager fonctionnel avec `send_message` et auto-resume
- WebSocket opérationnel avec `process_state` et `active_processes`
- Frontend avec textarea/bouton et gestion des états
