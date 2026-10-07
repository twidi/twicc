// Panel-owned refs let each recursive card subscribe to its own node and bar.
// Passing changing maps or clocks as props would update every card in the tree.
// Each view also provides visibility so its retained cards can suspend live reads.
export const SESSION_TREE_CONTEXT = Symbol('orchestration-session-tree')
export const AGENT_TREE_CONTEXT = Symbol('orchestration-agent-tree')
