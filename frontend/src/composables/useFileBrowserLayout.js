import { useContainerBreakpoint } from './useContainerBreakpoint'

/**
 * Width (in rem) under which the file-browsing panels (Files, Artifacts, Git) swap their
 * side-by-side tree + content layout for the stacked one with the file selector on top.
 * Single source of truth: every such panel must use `useFileBrowserLayout`.
 */
export const FILE_BROWSER_STACKED_BELOW_REM = 40

/**
 * Container query on the panel's own rendered width (see `useContainerBreakpoint`).
 *
 * @returns {{ isMobile: import('vue').Ref<boolean> }} `true` when the panel uses the stacked layout.
 */
export function useFileBrowserLayout() {
    const { isBelowBreakpoint: isMobile } = useContainerBreakpoint({
        breakpointRem: FILE_BROWSER_STACKED_BELOW_REM,
    })
    return { isMobile }
}
