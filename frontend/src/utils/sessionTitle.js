// Real sessions receive automatic titles from the backend. Only ephemeral
// sessions need the frontend suggestion flow because they have no database row.
export function shouldRequestAutomaticTitle(session, { titleAutoApply, titleGenerationEnabled }) {
    return !!(session?.ephemeral && titleAutoApply && titleGenerationEnabled)
}

export function showAutomaticTitleHint(session) {
    return session?.title_origin === 'auto' && !session.has_pending_title
}

// Name shown for a session in lists: its title, "New session" for an untitled draft, else its id.
export function getSessionDisplayName(session) {
    if (session.draft && !session.title) return 'New session'
    return session.title || session.id
}

// Call after validation. Keep unchanged titles: Save confirms their user origin.
export function buildSessionTitlePatch(title) {
    return { title: title.trim() }
}
