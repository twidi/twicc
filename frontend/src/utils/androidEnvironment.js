import { ref } from 'vue'

// Deliberately independent of stores, router and notification helpers.
export const isAndroidApp = ref(false)
export const androidPushState = ref(null)
export function markAndroidApp() {
    if (isAndroidApp.value) return
    isAndroidApp.value = true
    window.dispatchEvent(new Event('twicc:android-ready'))
}
