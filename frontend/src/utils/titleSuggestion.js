import { resolveTitleSuggestionModel, titleModelForProvider } from '../constants.js'

export function buildTitleSuggestionRequest({
    sessionId,
    provider,
    systemPrompt,
    prompt = null,
    titleSuggestionModel,
    noFallback = false,
}) {
    const message = {
        type: 'suggest_title',
        sessionId,
        provider,
        systemPrompt,
        titleSuggestionModel: resolveTitleSuggestionModel(titleSuggestionModel),
    }
    if (prompt) message.prompt = prompt
    // Set when the user picked this provider explicitly: the backend must not
    // answer with another one if it fails.
    if (noFallback) message.noFallback = true
    return message
}

/**
 * The model that made a suggestion: the reply's ``titleModel``, else (a reply
 * without the model field) the model of the provider that made it.
 * @param {Object|null|undefined} entry - A ``titleSuggestions`` entry.
 * @returns {string|null} A ``TITLE_SUGGESTION_MODEL`` value, null when unknown.
 */
export function suggestionModel(entry) {
    return entry?.titleModel || titleModelForProvider(entry?.titleProvider)
}

/**
 * Whether a suggestion was made by another model than the one asked for.
 *
 * The models are compared when the reply names both: a Qwen title has no
 * provider, and a fallback from Qwen to the session provider's agent changes
 * the model, not the provider. Without them, the providers are compared.
 * @param {Object|null|undefined} entry - A ``titleSuggestions`` entry.
 * @returns {boolean} False when there is no suggestion.
 */
export function isFallbackEntry(entry) {
    if (!entry?.suggestion) return false
    if (entry.requestedModel && entry.titleModel) return entry.titleModel !== entry.requestedModel
    if (!entry.titleProvider || !entry.requestedProvider) return false
    return entry.titleProvider !== entry.requestedProvider
}
