/**
 * useSessionMute — the "mute this session" action.
 *
 * Single entry point for every UI that flips `mute_on_user_turn` (session
 * header bell, command palette). The write itself lives in
 * `utils/sessionMute.js`, which stays free of app imports so its unit test can
 * load it under plain node; what belongs here is the part that needs the store.
 *
 * The flag suppresses two things at once: the "agent finished working"
 * notification family (in-app toast, sound, browser notification, Apprise
 * push) and the session's unread state (the row's eye, the project and
 * workspace badges, the favicon, the sidebar's cross-filter promotion). The
 * unread half always applies, so flipping the flag is never a no-op — no
 * warning to show.
 */
import { useDataStore } from '../stores/data'

/**
 * Flip `mute_on_user_turn` on one session.
 *
 * @param {string} sessionId
 */
export function toggleSessionMute(sessionId) {
    const store = useDataStore()
    const session = store.getSession(sessionId)
    if (!session || session.draft) return
    store.setSessionMuteOnUserTurn(session.project_id, sessionId, !session.mute_on_user_turn)
}
