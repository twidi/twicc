// The share bundle has no Vue Router. Reused utils/composables lazy-import the app
// router (`await import('../router')`, the codebase's anti-cycle pattern); in
// read-only mode navigation never fires, so a stub is safe — and, crucially, it cuts
// the entire SPA views chain (ProjectView / HomeView / SettingsPopover …) out of the
// bundle that disabled code splitting (`codeSplitting: false`) would otherwise inline.
export const router = {
    push() {}, replace() {}, go() {}, back() {},
    currentRoute: { value: { params: {}, query: {}, path: '/' } },
    resolve: () => ({ href: '#' }),
}
export default router
