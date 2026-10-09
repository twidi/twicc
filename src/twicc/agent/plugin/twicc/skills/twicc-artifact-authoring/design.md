# HTML design — look like TwiCC

How an HTML artifact looks like TwiCC and follows the user's display settings. Applies to every HTML artifact, in the Artifacts tab and inline. Read `SKILL.md` first.

## Light and dark

This applies to **every** HTML artifact, in the tab and inline.

- Inside TwiCC, `prefers-color-scheme` follows the app's current scheme, live — not the operating system's.
- Declare `:root { color-scheme: light dark; }`. Without it, native controls (inputs, selects, scrollbars) stay light, and the browser paints an opaque white background behind the page in dark mode.
- Style both modes with `light-dark()` or `@media (prefers-color-scheme: dark)`. In JS, read `matchMedia('(prefers-color-scheme: dark)')` and listen to its `change` event (charts, canvas).
- Set explicit background and text colors for each mode: `--twicc-canvas` and `--twicc-text` already switch (see *TwiCC look*). Inline pages keep a transparent background in the chat: see `inline.md`.

## TwiCC look

Make every HTML artifact look like TwiCC: use its design tokens instead of hard-coded colors, fonts, and sizes.

- TwiCC injects the `--twicc-*` custom properties into every HTML artifact. They change nothing until the page uses them. They follow the app's theme and switch with the light/dark scheme on their own.
- Page background: `--twicc-canvas` is the color of TwiCC's page; `--twicc-canvas-background` is the full page background with its gradient glows (use it with `background:`). Use one of them for the page wherever it is not transparent, never white or black.
- Surfaces, for cards and panels on that page: `--twicc-surface`, `--twicc-surface-raised`, `--twicc-surface-lowered`, `--twicc-border`, `--twicc-overlay`.
- Text: `--twicc-text`, `--twicc-text-quiet`, `--twicc-text-link`.
- Roles: `--twicc-{accent|neutral|success|warning|danger}-{fill|on|border}-{loud|normal|quiet}`. `fill` is a background, `on` is the text color on that fill, `border` goes with it.
- Fields: `--twicc-field-bg`, `--twicc-field-border`, `--twicc-field-text`, `--twicc-field-placeholder`, `--twicc-field-height`, `--twicc-field-radius`, `--twicc-focus`, `--twicc-focus-ring` (a full `outline` value).
- Type: `--twicc-font`, `--twicc-font-heading`, `--twicc-font-mono`, `--twicc-font-size-{2xs|xs|s|m|l|xl|2xl|3xl}`, `--twicc-font-weight-{light|normal|semibold|bold}`, `--twicc-line-height`, `--twicc-line-height-condensed`, `--twicc-line-height-expanded`.
- Space, shape, depth, motion: `--twicc-space-{3xs|2xs|xs|s|m|l|xl|2xl|3xl}`, `--twicc-radius-{s|m|l|pill}`, `--twicc-border-width`, `--twicc-shadow-{s|m|l}`, `--twicc-transition-fast`, `--twicc-transition-normal`, `--twicc-easing`.

For ready-made styles, link the kit: `<link rel="stylesheet" href="/_twicc/artifact-theme/kit.css">`.

- It styles the page root (font size setting, font, text, background, `color-scheme: light dark`, transparent while inline), headings, links, code, tables, buttons, inputs, selects, and textareas.
- Classes: `twicc-accent` or `twicc-danger` on a button; `twicc-card`, `twicc-stack` (vertical flex), `twicc-row` (wrapping flex); `twicc-callout` and `twicc-badge`, with `twicc-accent`, `twicc-success`, `twicc-warning`, or `twicc-danger`; `twicc-quiet` for secondary text.
- Every kit rule has zero specificity: any rule of the page overrides it.

```html
<link rel="stylesheet" href="/_twicc/artifact-theme/kit.css">
<div class="twicc-card twicc-stack">
  <div class="twicc-row"><label for="name">Name</label><input id="name"></div>
  <div class="twicc-row"><button class="twicc-accent">Save</button><button>Cancel</button></div>
  <span class="twicc-badge twicc-success">done</span>
</div>
```

## User preferences

The user's TwiCC display settings reach every HTML artifact shown inside TwiCC.

- **Font size**: the root carries the user's font size setting as `--twicc-root-font-size`. Apply it with `:root { font-size: var(--twicc-root-font-size, 100%); }` (the kit does). Then size **everything in `rem`** (or with the tokens, which are rem-based): text, spacing, widths, heights, radii, icons. Never use `px` for sizes, or the page ignores the setting. Only hairlines (1px borders) may stay in `px`.
- **Reduced motion**: the root has `data-twicc-reduce-motion` when the system or the "Reduce effects" setting asks for it. Then remove movement (slides, zooms, parallax, autoplay); keep fades and state indicators.
- **Reduce effects**: the root has `data-twicc-reduce-effects` when the user turned on "Reduce effects". Then also drop costly effects: blur and `backdrop-filter`, filters, heavy shadows, continuous animations.

```css
:root[data-twicc-reduce-motion] .panel { transition: opacity var(--twicc-transition-fast); }
:root[data-twicc-reduce-effects] .glass { backdrop-filter: none; }
```
