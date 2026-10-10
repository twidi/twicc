// The share bundle does not ship Mermaid's ELK layout. Mermaid 12 bundles ELK
// (~1.5 MB) behind a lazy import, which the viewer's disabled code splitting
// (`codeSplitting: false`) would inline into the single file even though
// diagrams render with the dagre layout (utils/mermaid.js). Before Mermaid 12
// ELK was a separate package we never installed, so a diagram that asks for it
// explicitly (`flowchart-elk`, `layout: elk`) fails to render here, as it always
// did.
export async function render() {
    throw new Error('The ELK layout is not available in the share viewer')
}
