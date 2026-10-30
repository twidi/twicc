// Option filtering for FilterableSelect.vue (kept pure so node --test covers it).

/**
 * The options shown for `query`: those whose label or keywords contain it (case-insensitive),
 * in the given order. Label and keywords are matched separately, never across both.
 */
export function filterSelectOptions(options, query) {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(o => o.label.toLowerCase().includes(q) || (o.keywords ?? '').toLowerCase().includes(q))
}
