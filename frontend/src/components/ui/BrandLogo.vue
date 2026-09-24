<script setup>
// The TwiCC logo (the tilted "CC" robot), drawn inline so it never depends on a
// public asset URL (the share viewers run on another host/base). Picks the
// artwork for the displayed size: below 24px the ears and antenna are dropped
// (same artwork as `public/favicon.svg`); from 24px up the full robot (same
// artwork as `public/robot-brand.svg`), split into parts so they can move on
// their own. Keep both in sync with those files.
//
// With `animated` (full robot only), the robot plays a loop: level → leans
// (and drifts) right → leans (and drifts) left → jumps back to the centre,
// straightening up in the air → lands level and blinks. Antenna and ears follow with a bit of inertia. Disabled for users who
// ask for reduced motion.
import { computed } from 'vue'

const props = defineProps({
    // Displayed size in CSS pixels (the logo is square).
    size: { type: Number, default: 32 },
    animated: { type: Boolean, default: false },
})

const SMALL_LOGO_MAX_SIZE = 24
const BRAND_COLOR = '#3178c0'

const isSmall = computed(() => props.size < SMALL_LOGO_MAX_SIZE)
</script>

<template>
    <svg v-if="isSmall" class="brand-logo" viewBox="0 0 64 64" :width="size" :height="size" aria-hidden="true">
        <g transform="rotate(-10 32 32)">
            <rect x="5" y="7" width="54" height="50" rx="15" :fill="BRAND_COLOR" />
            <path
                d="M27.63 23.86 A8 8 0 1 0 27.63 34.14 M48.63 23.86 A8 8 0 1 0 48.63 34.14"
                fill="none" stroke="#fff" stroke-width="6.5" stroke-linecap="round"
            />
            <rect x="26.5" y="45" width="11" height="4.5" rx="2.25" fill="#fff" />
        </g>
    </svg>
    <span
        v-else
        class="brand-logo"
        :class="{ 'brand-logo--animated': animated }"
        :style="{ width: `${size}px`, height: `${size}px` }"
        aria-hidden="true"
    >
        <!-- The jump (.hop) wraps the lean (.tilt) so it always goes straight up on screen. -->
        <span class="hop">
            <span class="tilt">
                <svg viewBox="0 0 64 64">
                    <g transform="translate(32 36) rotate(-10) scale(0.95) translate(-32 -36)">
                        <g :fill="BRAND_COLOR">
                            <rect class="ear-left" x="1" y="28" width="8" height="14" rx="3" />
                            <rect class="ear-right" x="55" y="28" width="8" height="14" rx="3" />
                            <g class="antenna">
                                <path d="M32 15V6" :stroke="BRAND_COLOR" stroke-width="4" stroke-linecap="round" />
                                <circle cx="32" cy="6" r="4" />
                            </g>
                            <rect x="9" y="14" width="46" height="42" rx="12" />
                        </g>
                        <path
                            class="eyes"
                            d="M27.98 29.82 A6.5 6.5 0 1 0 27.98 38.18 M45.48 29.82 A6.5 6.5 0 1 0 45.48 38.18"
                            fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"
                        />
                        <rect x="26" y="46" width="12" height="4" rx="2" fill="#fff" />
                    </g>
                </svg>
            </span>
        </span>
        <span v-if="animated" class="shadow"></span>
    </span>
</template>

<style scoped>
.brand-logo {
    display: inline-block;
    vertical-align: middle;
    flex-shrink: 0;
}

span.brand-logo {
    position: relative;
}

.hop,
.tilt {
    display: block;
    width: 100%;
    height: 100%;
}

.hop svg {
    display: block;
    width: 100%;
    height: 100%;
    overflow: visible;
}

/* Pivots: the head leans around the same point the artwork's own -10° tilt
   uses ((32, 36) of the 64 grid), so "level" is exactly centred over the
   shadow; the jump squashes from the feet; each part rotates around its own
   attachment point. */
.tilt { transform-origin: 50% 56.25%; }
.hop { transform-origin: 50% 95%; }
.antenna, .ear-left, .ear-right, .eyes { transform-box: fill-box; }
.antenna { transform-origin: 50% 100%; }
.ear-left { transform-origin: 100% 50%; }
.ear-right { transform-origin: 0% 50%; }
.eyes { transform-origin: 50% 50%; }

.shadow {
    position: absolute;
    left: 22%;
    right: 22%;
    bottom: -7%;
    height: 7%;
    border-radius: 50%;
    background: rgb(0 0 0 / 14%);
}

/* One 5.2s loop shared by every part. The artwork is tilted -10°, so on .tilt
   rotate(10deg) = level, 20deg = leaning right, 0deg = leaning left. */
.brand-logo--animated :is(.tilt, .hop, .shadow, .antenna, .eyes, .ear-left, .ear-right) {
    animation-duration: 5.2s;
    animation-iteration-count: infinite;
    animation-timing-function: ease-in-out;
}
.brand-logo--animated .tilt { animation-name: brand-logo-tilt; }
.brand-logo--animated .hop { animation-name: brand-logo-hop; }
.brand-logo--animated .shadow { animation-name: brand-logo-shadow; }
.brand-logo--animated .antenna { animation-name: brand-logo-antenna; }
.brand-logo--animated .eyes { animation-name: brand-logo-blink; }
.brand-logo--animated .ear-left { animation-name: brand-logo-ear-left; }
.brand-logo--animated .ear-right { animation-name: brand-logo-ear-right; }

/* Level → right → left → (jump) → level. */
@keyframes brand-logo-tilt {
    0%, 10% { transform: rotate(10deg); }
    19%, 30% { transform: rotate(20deg); }
    42%, 51% { transform: rotate(0deg); }
    60%, 100% { transform: rotate(10deg); }
}

/* Drifts right with the right lean, left with the left lean (7% of its size),
   then the jump — squash, stretch up, land squashed, small rebound — brings it
   back to the centre. */
@keyframes brand-logo-hop {
    0%, 10% { transform: translate(0, 0) scale(1, 1); }
    19%, 30% { transform: translate(7%, 0) scale(1, 1); }
    42%, 48% { transform: translate(-7%, 0) scale(1, 1); }
    51% { transform: translate(-7%, 0) scale(1.1, 0.88); }
    56% { transform: translate(-2.8%, -20%) scale(0.94, 1.08); animation-timing-function: ease-in; }
    62% { transform: translate(0, 0) scale(1.12, 0.86); }
    66% { transform: translate(0, 0) scale(0.97, 1.03); }
    70%, 100% { transform: translate(0, 0) scale(1, 1); }
}

/* The shadow follows the drift at a quarter of the distance (its own width is 56%
   of the logo, so 3.125% of it = 1.75% of the logo). */
@keyframes brand-logo-shadow {
    0%, 10% { transform: translateX(0) scaleX(1); opacity: 1; }
    19%, 30% { transform: translateX(3.125%) scaleX(1); }
    42%, 48% { transform: translateX(-3.125%) scaleX(1); }
    51% { transform: translateX(-3.125%) scaleX(1.08); }
    56% { transform: translateX(-1.25%) scaleX(0.6); opacity: 0.4; }
    62% { transform: translateX(0) scaleX(1.1); opacity: 1; }
    70%, 100% { transform: translateX(0) scaleX(1); }
}

/* Eyes only (the mouth stays still), right when the feet touch the ground. */
@keyframes brand-logo-blink {
    0%, 62% { transform: scaleY(1); }
    63.5%, 65% { transform: scaleY(0.1); }
    67.5%, 100% { transform: scaleY(1); }
}

/* Lags behind each lean, then springs after the landing. */
@keyframes brand-logo-antenna {
    0%, 10% { transform: rotate(0); }
    15% { transform: rotate(-12deg); }
    21% { transform: rotate(7deg); }
    25% { transform: rotate(-3deg); }
    30% { transform: rotate(0); }
    36% { transform: rotate(12deg); }
    43% { transform: rotate(-6deg); }
    46% { transform: rotate(2deg); }
    48% { transform: rotate(0); }
    56% { transform: rotate(-8deg); }
    62% { transform: rotate(0); }
    65% { transform: rotate(-14deg); }
    69% { transform: rotate(10deg); }
    73% { transform: rotate(-5deg); }
    77% { transform: rotate(2deg); }
    81%, 100% { transform: rotate(0); }
}

/* Ears pop out in the air, settle after the landing. */
@keyframes brand-logo-ear-left {
    0%, 51% { transform: translateX(0) rotate(0); }
    56% { transform: translateX(-2.5px) rotate(-12deg); }
    62% { transform: translateX(0) rotate(0); }
    65% { transform: translateX(-1px) rotate(-4deg); }
    68%, 100% { transform: translateX(0) rotate(0); }
}

@keyframes brand-logo-ear-right {
    0%, 51% { transform: translateX(0) rotate(0); }
    56% { transform: translateX(2.5px) rotate(12deg); }
    62% { transform: translateX(0) rotate(0); }
    65% { transform: translateX(1px) rotate(4deg); }
    68%, 100% { transform: translateX(0) rotate(0); }
}

@media (prefers-reduced-motion: reduce) {
    .brand-logo--animated :is(.tilt, .hop, .shadow, .antenna, .eyes, .ear-left, .ear-right) {
        animation: none;
    }
}
</style>
