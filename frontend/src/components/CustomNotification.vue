<script setup>
/**
 * CustomNotification - A toast notification component that mirrors Notivue's
 * built-in Notification component but allows rich HTML content in the message area.
 *
 * Uses the exact same CSS classes, structure, icons, and theming as Notivue's
 * Notification component so it looks identical. The only difference is that
 * instead of rendering the message as plain text in a <p>, it provides a slot
 * for arbitrary content.
 *
 * Usage (via useToast):
 *   toast.custom(MyComponent, { title: 'Done', type: 'success', props: { ... } })
 *
 * Or with raw HTML:
 *   toast.custom(null, { title: 'Done', type: 'success', html: '<strong>Bold</strong> text' })
 */
import { shallowRef, watch, computed } from 'vue'
import { filledIcons, NotificationProgress, useNotivue } from 'notivue'

const props = defineProps({
    item: {
        type: Object,
        required: true,
    },
    theme: {
        type: Object,
        default: () => ({}),
    },
    icons: {
        type: Object,
        default: () => filledIcons,
    },
    hideClose: {
        type: Boolean,
        default: false,
    },
    closeAriaLabel: {
        type: String,
        default: 'Close',
    },
})

// Reactive icon based on notification type (mirrors Notivue's behavior)
const Icon = shallowRef(props.icons[props.item.type])
watch(
    () => props.item.type,
    (t) => (Icon.value = props.icons[t]),
    { flush: 'sync' },
)
const CloseIcon = props.icons.close

const hasTitle = computed(() => Boolean(props.item.title))

// Access the content component and its props from item.props (set by useToast)
const contentComponent = computed(() => props.item.props?.content)
const contentProps = computed(() => props.item.props?.contentProps || {})
const contentHtml = computed(() => props.item.props?.html)

// Merge theme with optional style overrides from item.props (e.g. --nv-width)
const mergedStyle = computed(() => ({
    ...props.theme,
    ...props.item.props?.style,
}))
</script>

<template>
    <div
        class="Notivue__notification"
        :data-notivue="item.type"
        :data-notivue-has-title="hasTitle"
        :style="mergedStyle"
    >
        <!-- Icon -->
        <template v-if="Icon">
            <component
                :is="Icon"
                v-if="typeof Icon === 'object'"
                class="Notivue__icon"
                aria-hidden="true"
            />
            <div v-else-if="typeof Icon === 'string'" class="Notivue__icon" aria-hidden="true">
                {{ Icon }}
            </div>
        </template>

        <!-- Content -->
        <div
            class="Notivue__content"
            :aria-live="item.ariaLive"
            :role="item.ariaRole"
            aria-atomic="true"
        >
            <!-- Title (same as Notivue: plain text in h3) -->
            <h3 v-if="item.title" class="Notivue__content-title" v-text="item.title" />

            <!-- Rich content area: slot, component, HTML, or fallback to plain message -->
            <div class="Notivue__content-message">
                <slot>
                    <component
                        v-if="contentComponent"
                        :is="contentComponent"
                        v-bind="contentProps"
                    />
                    <div v-else-if="contentHtml" v-html="contentHtml" />
                    <template v-else>{{ item.message }}</template>
                </slot>
            </div>
        </div>

        <!-- Close button -->
        <button
            v-if="!hideClose && CloseIcon && item.type !== 'promise'"
            class="Notivue__close"
            :aria-label="closeAriaLabel"
            type="button"
            tabindex="-1"
            @click="item.clear"
        >
            <component
                :is="CloseIcon"
                v-if="typeof CloseIcon === 'object'"
                class="Notivue__close-icon"
            />
            <div v-else-if="typeof CloseIcon === 'string'" aria-hidden="true">
                {{ CloseIcon }}
            </div>
        </button>

        <!-- Progress bar -->
        <NotificationProgress :item="item" />
    </div>
</template>

<style scoped>
/* Ensure the content area respects the notification's max-width
   so that children (like session toast rows) can truncate properly.
   The flex chain (notification → content → message) needs min-width: 0
   at each level to allow text-overflow: ellipsis to work. */
.Notivue__content {
    min-width: 0;
}
.Notivue__content-message {
    overflow: hidden;
}
</style>
