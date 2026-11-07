// Shared constants/helpers of the responsive-viewport mode (ViewportStage +
// ViewportToolbar), extracted from BrowserPane so the artifact HTML preview
// reuses the exact same presets and bounds.

export const VIEWPORT_PRESETS = [
    { label: 'iPhone SE', width: 375, height: 667 },
    { label: 'iPhone 15', width: 393, height: 852 },
    { label: 'iPhone 15 Pro Max', width: 430, height: 932 },
    { label: 'Pixel 8', width: 412, height: 915 },
    { label: 'Galaxy S24', width: 360, height: 780 },
    { label: 'iPad Mini', width: 768, height: 1024 },
    { label: 'iPad Pro 11"', width: 834, height: 1194 },
    { label: 'iPad Pro 12.9"', width: 1024, height: 1366 },
    { labelKey: 'preview.viewport.presets.laptop', width: 1280, height: 800 },
    { labelKey: 'preview.viewport.presets.largeLaptop', width: 1440, height: 900 },
    { labelKey: 'preview.viewport.presets.desktop', width: 1920, height: 1080 },
]

export const VIEWPORT_MIN = 100
export const VIEWPORT_MAX = 8000

export function clampViewportSize(value) {
    return Math.min(VIEWPORT_MAX, Math.max(VIEWPORT_MIN, Math.round(value)))
}
