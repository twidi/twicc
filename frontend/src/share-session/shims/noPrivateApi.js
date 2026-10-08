// Reused transcript components use their injected share API for public reads.
// Fail closed if an unreachable private action calls the SPA fetch helper.
export async function apiFetch() {
    throw new Error('Private API unavailable in a shared conversation')
}
export async function handleUnauthorized() {}
