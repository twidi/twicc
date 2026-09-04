export function applySessionItemsAdded(store, message) {
    const session = store.getSession(message.session_id)
    if (session?.compute_version_up_to_date === false) return false

    if (message.items?.length) {
        store.markItemsLive(message.session_id, message.items.map(item => item.line_num))
    }
    if (store.areSessionItemsFetched(message.session_id)) {
        store.addSessionItems(message.session_id, message.items, message.updated_metadata)
    }
    return true
}
