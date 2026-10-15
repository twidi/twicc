// frontend/src/utils/timeline/providerLabels.js
//
// Adapter between the provider helpers (providers/baseHelpers.js) and the settings diff of a timeline row.
// The helpers pull in stores and the DOM, so they are handed in: the module itself stays pure.

/**
 * @param {(provider: string) => (Object|null)} getProviderHelpers
 * @returns {(provider: (string|null)) => (import('./settingsDiff.js').SettingsLabels|null)} `null` for an
 *   unknown provider: the diff then shows raw names and values.
 */
export function createLabelsFor(getProviderHelpers) {
    return (provider) => {
        const helpers = provider ? getProviderHelpers(provider) : null
        if (!helpers) return null
        return {
            field: (field) => helpers.getFieldLabel(field),
            choice: (field, value) => helpers.getChoiceLabel(field, value),
            model: (model) => helpers.getModelLabel(model),
        }
    }
}
