export const SETTINGS_POPOVER_WIDTH_RATIO = 0.9
export const SETTINGS_POPOVER_MAX_WIDTH = 700
export const SETTINGS_POPOVER_MARGIN = 16

export function resolveSettingsPlacement({ preferred, anchorRight, innerWidth }) {
    const width = Math.min(SETTINGS_POPOVER_WIDTH_RATIO * innerWidth, SETTINGS_POPOVER_MAX_WIDTH)
    return preferred.startsWith('right') && innerWidth < anchorRight + width + SETTINGS_POPOVER_MARGIN
        ? 'top'
        : preferred
}
