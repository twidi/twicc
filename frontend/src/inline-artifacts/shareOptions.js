/** Existing shares and new shares include inline artifacts by default. */
export function includeInlineArtifacts(options) {
    return options?.include_inline_artifacts ?? true
}
