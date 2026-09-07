// Optional live updates for a mode="live" session share (Phase 5). Connects to
// ws/share/<token>/, appends filtered items into the shim store, refreshes meta.
export function connectShareLive({ tokenPath, sessionId, onItems, onMeta, onToolState, onProcessState, onAgentLink, onAgentStopped, onAgentIdle, onClosed }) {
    const wsBase = location.origin.replace(/^http/, 'ws')
    const token = tokenPath.replace(/^\/share\//, '').replace(/\/+$/, '')
    let ws = null, closed = false, backoff = 1000
    function open() {
        if (closed) return
        ws = new WebSocket(`${wsBase}/ws/share/${token}/`)
        ws.onmessage = (ev) => {
            const msg = JSON.parse(ev.data)
            if (msg.type === 'share_items_added') onItems(msg.items, msg.session_id)
            else if (msg.type === 'share_meta') onMeta(msg.meta)
            else if (msg.type === 'share_tool_state') onToolState?.(msg)
            else if (msg.type === 'share_process_state') onProcessState?.(msg)
            else if (msg.type === 'share_agent_idle') onAgentIdle?.(msg)
            else if (msg.type === 'share_agent_stopped') onAgentStopped?.(msg)
            else if (msg.type === 'share_agent_link') onAgentLink?.(msg.link)
            else if (msg.type === 'share_closed') { closed = true; onClosed?.() }
        }
        ws.onopen = () => { backoff = 1000 }
        ws.onclose = () => { if (!closed) { setTimeout(open, backoff); backoff = Math.min(backoff * 2, 15000) } }
    }
    open()
    return () => { closed = true; ws?.close() }
}
