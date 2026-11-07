import { ref, watch } from 'vue'

/** Publish only the latest page/language result, including asynchronous rendering. */
export function useLocalizedContentBody(key, locale, load) {
    const bodyHtml = ref('')
    const contentLocale = ref(locale.value)
    const loading = ref(false)
    const errored = ref(false)

    watch([key, locale], async ([currentKey, currentLocale], _, onCleanup) => {
        let active = true
        onCleanup(() => { active = false })
        bodyHtml.value = ''
        errored.value = false
        loading.value = !!currentKey
        if (!currentKey) return

        try {
            const content = await load(currentKey, currentLocale)
            if (!active) return
            bodyHtml.value = content.html
            contentLocale.value = content.locale
        } catch {
            if (active) errored.value = true
        } finally {
            if (active) loading.value = false
        }
    }, { immediate: true })

    return { bodyHtml, contentLocale, loading, errored }
}
