// Overlay entrances and exits (visual refresh step 5b).
// Design: docs/plans/2026-09-28-overlay-motion-design.md §4, §5, §14.
//
// Web Awesome animates an overlay by adding a class (`show`, `hide`, `show-with-scale`…)
// to one element of its shadow DOM, and waits for that animation's `animationend`. TwiCC
// keeps that lifecycle and only restyles the animation: one extra stylesheet per element,
// appended to its Lit `elementStyles`, so it comes last in every shadow root and its
// `animation` declarations win over Web Awesome's (same selectors or more specific).
//
// Model: one keyframe per moving part, played forward to open and in reverse to close. A
// close during an opening then updates the running animation in place (never cancels
// it: a cancel would resolve Web Awesome's pending hide at once). `reverse` also reverses
// the easing, so exits declare `ease-out` to play as ease-in. Every distance and scale
// delta is multiplied by --motion-amount (0 under reduced motion: fades only).
//
// Glass overlays keep their blur while they move (§14): an opacity animation on an
// ancestor of a glass layer stops its blur for the whole animation. The element that moves
// only moves (translate, scale); the fade animates the registered --twicc-reveal (0 → 1,
// motion.css) on it, which the glass layers read as their own opacity (glass.css), the
// content as filter: opacity() during the motion, and the cast shadow as alphas. The
// reveal eases with --motion-ease next to a spring movement, --motion-ease-out otherwise.
// Tooltips keep an opacity animation (their text sits in a display: contents wrapper that
// a filter cannot fade); the color picker is not glass.

const A = 'var(--motion-amount)'

/** One entry per Web Awesome element whose show/hide animation TwiCC restyles. */
export const WA_MOTION_STYLES = {
    // The dialog and its ::backdrop (the glass veil) last the same in each state: the
    // first animationend of either resolves Web Awesome's wait. The veil keeps its own
    // opacity fade (it has no glass layer inside).
    'wa-dialog': `
        .dialog.show { animation: twicc-dialog 320ms var(--motion-ease-spring), twicc-reveal 320ms var(--motion-ease); }
        .dialog.show::backdrop { animation: twicc-fade 320ms var(--motion-ease-out); }
        .dialog.hide { animation: twicc-dialog 160ms ease-out reverse forwards, twicc-reveal 160ms ease-out reverse forwards; }
        .dialog.hide::backdrop { animation: twicc-fade 160ms ease-out reverse forwards; }

        /* Drop-in dialogs (command palette, search overlay). */
        :host(.motion-drop) .dialog.show { animation: twicc-drop 260ms var(--motion-ease-spring), twicc-reveal 260ms var(--motion-ease); }
        :host(.motion-drop) .dialog.show::backdrop { animation: twicc-fade 260ms var(--motion-ease-out); }
        :host(.motion-drop) .dialog.hide { animation: twicc-drop 160ms ease-out reverse forwards, twicc-reveal 160ms ease-out reverse forwards; }
        :host(.motion-drop) .dialog.hide::backdrop { animation: twicc-fade 160ms ease-out reverse forwards; }

        /* The content fades during the motion (the glass layers read --twicc-reveal). */
        .dialog.show > *, .dialog.hide > * { filter: opacity(var(--twicc-reveal)); }

        /* Click outside a dialog without light dismiss: a feedback nudge, scale only; under
           reduced motion (--motion-amount: 0), a --twicc-reveal dip of the whole dialog
           instead, its blur kept. */
        .dialog.pulse { animation: twicc-pulse 250ms var(--motion-ease); }
        .dialog.pulse > * { filter: opacity(var(--twicc-reveal)); }

        @keyframes twicc-dialog { from { translate: 0 calc(0.75rem * ${A}); scale: calc(1 - 0.04 * ${A}); } }
        @keyframes twicc-drop { from { translate: 0 calc(-0.5rem * ${A}); scale: calc(1 - 0.03 * ${A}); } }
        @keyframes twicc-fade { from { opacity: 0; } }
        @keyframes twicc-reveal { from { --twicc-reveal: 0; } }
        @keyframes twicc-pulse { 50% { scale: calc(1 + 0.02 * ${A}); --twicc-reveal: calc(1 - 0.15 * (1 - ${A})); } }
    `,
    // Menus grow from Web Awesome's placement-based origin (the edge touching the trigger).
    'wa-dropdown': `
        #menu.show { animation: twicc-pop-move 180ms var(--motion-ease-out), twicc-reveal 180ms var(--motion-ease-out); }
        #menu.hide { animation: twicc-pop-move 50ms ease-out reverse forwards, twicc-reveal 50ms ease-out reverse forwards; }
        #menu.show ::slotted(*), #menu.hide ::slotted(*) { filter: opacity(var(--twicc-reveal)); }

        @keyframes twicc-pop-move { from { scale: calc(1 - 0.06 * ${A}); } }
        @keyframes twicc-reveal { from { --twicc-reveal: 0; } }
    `,
    // Submenus fade only: a scale would make #submenu the containing block of its fixed
    // "safe triangle" (#submenu::before), which would stop working during the entrance.
    'wa-dropdown-item': `
        #submenu.show { animation: twicc-reveal 180ms var(--motion-ease-out); }
        #submenu.hide { animation: twicc-reveal 50ms ease-out reverse forwards; }
        #submenu.show ::slotted(*), #submenu.hide ::slotted(*) { filter: opacity(var(--twicc-reveal)); }

        @keyframes twicc-reveal { from { --twicc-reveal: 0; } }
    `,
    // Popovers grow from their arrow (the button), measured by utils/glassArrowGap.js;
    // without a measured arrow, Web Awesome's origin applies. The body's content reads the
    // --twicc-reveal-filter its moving popup declares (none at rest).
    'wa-popover': `
        :host([data-glass-arrow='bottom']) .popover::part(popup) { transform-origin: var(--popover-arrow-center, 50%) bottom; }
        :host([data-glass-arrow='top']) .popover::part(popup) { transform-origin: var(--popover-arrow-center, 50%) top; }
        :host([data-glass-arrow='right']) .popover::part(popup) { transform-origin: right var(--popover-arrow-center, 50%); }
        :host([data-glass-arrow='left']) .popover::part(popup) { transform-origin: left var(--popover-arrow-center, 50%); }

        .body ::slotted(*) { filter: var(--twicc-reveal-filter, none); }
    `,
    // The inner popup of wa-select (host class select), wa-color-picker (color-popup),
    // wa-popover (popover) and wa-tooltip (tooltip). A bare wa-popup (the pickers) matches none.
    'wa-popup': `
        /* wa-select list box: a menu. */
        :host(.select) .popup.show { animation: twicc-pop-move 180ms var(--motion-ease-out), twicc-reveal 180ms var(--motion-ease-out); }
        :host(.select) .popup.hide { animation: twicc-pop-move 100ms ease-out reverse forwards, twicc-reveal 100ms ease-out reverse forwards; }

        /* wa-color-picker panel: a menu (not glass: it fades its opacity); Web Awesome gives
           it no origin. */
        :host(.color-popup) .popup.show-with-scale { animation: twicc-pop 180ms var(--motion-ease-out); }
        :host(.color-popup) .popup.hide-with-scale { animation: twicc-pop 100ms ease-out reverse forwards; }
        :host(.color-popup[data-current-placement='bottom-start']) .popup { transform-origin: left top; }
        :host(.color-popup[data-current-placement='bottom-end']) .popup { transform-origin: right top; }
        :host(.color-popup[data-current-placement='top-start']) .popup { transform-origin: left bottom; }
        :host(.color-popup[data-current-placement='top-end']) .popup { transform-origin: right bottom; }

        /* wa-popover body. */
        :host(.popover) .popup.show-with-scale { animation: twicc-grow 260ms var(--motion-ease-spring), twicc-reveal 260ms var(--motion-ease); }
        :host(.popover) .popup.hide-with-scale { animation: twicc-grow 100ms ease-out reverse forwards, twicc-reveal 100ms ease-out reverse forwards; }

        /* wa-tooltip: an opacity fade (its text cannot take a filter), blur off meanwhile. */
        :host(.tooltip) .popup.show-with-scale { animation: twicc-tip 160ms var(--motion-ease-out); }
        :host(.tooltip) .popup.hide-with-scale { animation: twicc-tip 100ms ease-out reverse forwards; }

        /* The glass contents (popover body, select options) live in other trees: they read
           this filter, resolved here each frame and inherited. */
        :host(.popover) .popup.show-with-scale,
        :host(.popover) .popup.hide-with-scale,
        :host(.select) .popup.show,
        :host(.select) .popup.hide { --twicc-reveal-filter: opacity(var(--twicc-reveal)); }

        @keyframes twicc-pop { from { opacity: 0; scale: calc(1 - 0.06 * ${A}); } }
        @keyframes twicc-pop-move { from { scale: calc(1 - 0.06 * ${A}); } }
        @keyframes twicc-grow { from { scale: calc(1 - 0.04 * ${A}); } }
        @keyframes twicc-reveal { from { --twicc-reveal: 0; } }
        @keyframes twicc-tip { from { opacity: 0; scale: calc(1 - 0.04 * ${A}); } }
    `,
    // The select's options fade with the reveal of its moving popup. The opt-in direct
    // list boxes fade as a whole through their own opacity (glass.css): excluded here.
    'wa-select': `
        :host(:not(.glass-listbox-direct)) .listbox ::slotted(*) { filter: var(--twicc-reveal-filter, none); }
    `,
}

const INSTALLED = Symbol('twicc.waMotionStyles')

function createConstructedSheet(css) {
    const sheet = new CSSStyleSheet()
    sheet.replaceSync(css)
    return sheet
}

/**
 * Append a constructed stylesheet to each element's Lit `elementStyles`, so every shadow
 * root created afterwards adopts it after Web Awesome's own styles.
 * Skips an element that is not defined (the share viewer and the artifact shell register
 * a subset) or whose `elementStyles` is not an array. Idempotent (a Symbol marker on the
 * constructor).
 * @param {{ registry?: CustomElementRegistry, createSheet?: (css: string) => CSSStyleSheet }} deps
 * @returns {string[]} the tags actually patched
 */
export function installWaMotionStyles({ registry = globalThis.customElements, createSheet = createConstructedSheet } = {}) {
    const patched = []
    if (!registry) return patched
    for (const [tag, css] of Object.entries(WA_MOTION_STYLES)) {
        const ctor = registry.get(tag)
        if (!ctor || Object.hasOwn(ctor, INSTALLED) || !Array.isArray(ctor.elementStyles)) continue
        ctor.elementStyles.push(createSheet(css))
        ctor[INSTALLED] = true
        patched.push(tag)
    }
    return patched
}
