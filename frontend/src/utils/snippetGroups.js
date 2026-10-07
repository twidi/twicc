export function isSnippetGroup(entry) {
    return entry?.type === 'group' && Array.isArray(entry.items)
}

export function createSnippetGroup(label) {
    return { type: 'group', id: crypto.randomUUID(), label: label.trim(), items: [] }
}

export function getGroupItems(entries, groupId = null) {
    if (!groupId) return entries
    return entries.find((entry) => isSnippetGroup(entry) && entry.id === groupId)?.items || null
}

export function flattenGroupedEntries(entries) {
    return entries.flatMap((entry) => isSnippetGroup(entry) ? entry.items : [entry])
}

/** Apply display metadata to both groups and their children without modifying saved data. */
export function mapGroupedEntries(entries, transform) {
    return entries.map((entry) => transform(isSnippetGroup(entry)
        ? { ...entry, items: entry.items.map(transform) }
        : entry))
}

/** Destination indices describe insertion points before removal from the source list. */
export function moveGroupedEntry(entries, from, to) {
    const source = getGroupItems(entries, from.groupId)
    const destination = getGroupItems(entries, to.groupId)
    if (!source || !destination || !Number.isInteger(from.index) || !Number.isInteger(to.index)
        || from.index < 0 || from.index >= source.length || to.index < 0 || to.index > destination.length) return false
    const item = source[from.index]
    if (isSnippetGroup(item) && to.groupId) return false
    const target = source === destination && from.index < to.index ? to.index - 1 : to.index
    if (source === destination && from.index === target) return false
    source.splice(from.index, 1)
    destination.splice(target, 0, item)
    return true
}

export function updateGroupedEntry(entries, from, item, targetGroupId = null) {
    const source = getGroupItems(entries, from.groupId)
    const destination = getGroupItems(entries, targetGroupId)
    if (!source || !destination || !Number.isInteger(from.index) || !source[from.index]) return false
    if (source === destination) source[from.index] = item
    else { source.splice(from.index, 1); destination.push(item) }
    return true
}

export function removeSnippetGroup(entries, id) {
    const index = entries.findIndex((entry) => isSnippetGroup(entry) && entry.id === id)
    if (index < 0) return false
    entries.splice(index, 1, ...entries[index].items)
    return true
}

export function renameSnippetGroup(entries, id, label) {
    const group = entries.find((entry) => isSnippetGroup(entry) && entry.id === id)
    if (!group || !label.trim()) return false
    group.label = label.trim()
    return true
}
