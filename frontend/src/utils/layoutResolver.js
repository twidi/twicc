// layoutResolver.js — PURE dockable-layout resolver. No DOM, no side effects, no Vue.
// Ported verbatim from the design prototype (see docs/plans/2026-06-16-dockable-layout-design.md
// and -impl-step1.md). Single source of all layout rules + thresholds. Mirrors the
// computeVisualItems pattern: raw inputs -> pure transform -> dumb render. Keep it pure
// (no imports, unit-testable in isolation).
//
// Contract:
//   resolveLayout(input) -> renderDescription
//
// input = {
//   tabs:       [{ id, label, icon, optional?, hasContent?, fixedCenter? }]
//               An optional tab with no content is treated as ABSENT (filtered out): it yields
//               no tab, no dock, no gutter, no overlay — exactly as if it were never passed in.
//   assignment: { [tabId]: dockId | 'center' }   // chat (fixedCenter) is always center
//   viewport:   { w, h }                          // session content area, in px
//   activeSide: 'left' | 'right'                  // which side wins under mutual exclusion
//   activeResize:'left' | 'right'                 // column with priority when resizing (pushes the other to its min)
//   collapsed:  string[]                          // docks the user manually minimized
//   maximized?: string[]                          // dockIds the maximized region holds ('center' for
//               the central zone); when set, one region fills everything and nothing else renders.
//   config?:    partial DEFAULT_CONFIG override
// }
//
// All thresholds live in DEFAULT_CONFIG — the single place to tune the rules.

export const DOCKS = [
  'left-top', 'left-bottom',
  'right-top', 'right-bottom',
  'bottom-left', 'bottom-right',
];

export const DEFAULT_CONFIG = {
  // --- Structural thresholds: decide the layout STRUCTURE (columns shown, merges, mode) ---
  centerMinW: 460,        // min comfortable width for the chat / center
  centerMinH: 220,        // min comfortable height for the center
  sideMinW: 300,          // min width for a persistent side column
  sideMergeBelowH: 600,   // side-column height below which the two vertical siblings merge into one tab bar
  bottomMinH: 150,        // min height for the bottom region (else it overlays)
  bottomMergeBelowW: 600, // bottom-span width below which the two bottom siblings merge into one tab bar
  bottomComfortW: 600,    // center-col width below which the bottom flips to full width (classic)
  mobileMaxW: 480,        // at or below this width -> pure tabs (no docks)
  overlayCoverage: 0.9,   // an opened overlay covers 90%, leaving a 10% escape strip
  railW: 30,              // px width/height of a collapsed-dock edge gutter (the thin icon rail) (keep updated with --gutter-size in DockGutter.vue)
  // --- Splitter ratios: the user-draggable layout intention (persisted, clamped at render) ---
  leftColFrac: 0.25,
  rightColFrac: 0.25,
  bottomFrac: 0.33,
  leftSplitFrac: 0.5,
  rightSplitFrac: 0.5,
  bottomSplitFrac: 0.5,
  siblingMaxFrac: 0.8,    // one sibling may take at most 80% of the space reserved for both (the other keeps ≥ 20%)
  // Dock resize clamps — how far a column/bottom boundary can be dragged vs the center.
  // Distinct from the structural mins above (which decide the layout STRUCTURE).
  centerResizeMinW: 300,  // center is never dragged narrower than this
  centerResizeMinH: 150,  // chat is never dragged shorter than this (caps how tall the bottom can grow)
  sideResizeMinW: 150,    // a side column is never dragged narrower than this
  bottomResizeMinH: 150,  // the bottom is never dragged shorter than this
};

const clamp = (v, lo, hi) => Math.max(lo, Math.min(Math.max(lo, hi), v));

const EDGE_DOCKS = {
  left: ['left-top', 'left-bottom'],
  right: ['right-top', 'right-bottom'],
  bottom: ['bottom-left', 'bottom-right'],
};

// Where a collapsed dock's icons sit within its edge gutter, mirroring its origin.
// start = top (left/right edges) or left (bottom edge); end = bottom or right.
const DOCK_ANCHOR = {
  'left-top': 'start', 'right-top': 'start', 'bottom-left': 'start',
  'left-bottom': 'end', 'right-bottom': 'end', 'bottom-right': 'end',
};

// A tab "exists" in the layout iff it is non-optional, or optional WITH content. An optional tab
// with no content is ABSENT: filtered out before bucketing, so it produces no dock / gutter /
// overlay. Mirrors reality — the app simply omits a not-yet-present tool (Git/Artifacts/...) from
// `tabs`; enforcing it here lets a caller pass the full roster + flags and get the same result.
const isPresent = (t) => !t.optional || t.hasContent;

function bucketTabs(tabs, assignment) {
  const byDock = Object.fromEntries(DOCKS.map((d) => [d, []]));
  const center = [];
  for (const t of tabs) {
    if (t.fixedCenter) { center.push(t); continue; }
    const a = assignment[t.id] || 'center';
    if (DOCKS.includes(a)) byDock[a].push(t);
    else center.push(t);
  }
  return { byDock, center };
}

// A dock "demands" space when it holds at least one tab and the user hasn't minimized it.
// Absent tabs (optional + no content) are filtered out upstream, so every bucketed tab counts.
function demandOf(byDock, dockId, collapsedSet) {
  return byDock[dockId].length > 0 && !collapsedSet.has(dockId);
}

function edgeTabs(byDock, edge) {
  return EDGE_DOCKS[edge].flatMap((d) => byDock[d]);
}

export function resolveLayout(input) {
  const cfg = { ...DEFAULT_CONFIG, ...(input.config || {}) };
  const { w: W, h: H } = input.viewport;
  const activeSide = input.activeSide || 'left';
  const activeResize = input.activeResize || 'left';
  const collapsed = new Set(input.collapsed || []);
  const { byDock, center } = bucketTabs((input.tabs || []).filter(isPresent), input.assignment || {});

  const decisions = [];
  const say = (s) => decisions.push(s);

  // ---- Maximized mode (highest priority): one region fills the whole area, with its tabs and
  // nothing else — no other docks, no gutters, no splitters, no overlays. The only exit is restore.
  // `maximized` is the list of dockIds the maximized region holds ('center' for the central zone);
  // a single id maximizes one dock, two a merged region. Transient — never persisted. If those docks
  // hold no (present) tabs, the region comes back empty and the wiring auto-restores. ----
  if (input.maximized && input.maximized.length) {
    const docks = input.maximized;
    const slots = docks.map((d) => d === 'center'
      ? { dockId: 'center', tabs: center }
      : { dockId: d, tabs: byDock[d] || [] });
    const merged = slots.length > 1;
    say(`maximized: ${docks.join('+')}`);
    return {
      mode: 'maximized',
      viewport: { w: W, h: H },
      regions: [{
        id: 'maximized', kind: 'maximized', x: 0, y: 0, w: W, h: H,
        label: docks.join(' + '), slots, merged,
        mergedFrom: merged ? docks.slice() : undefined,
      }],
      gutters: [], overlays: [], splitters: [], decisions,
    };
  }

  const leftDemand = demandOf(byDock, 'left-top', collapsed) || demandOf(byDock, 'left-bottom', collapsed);
  const rightDemand = demandOf(byDock, 'right-top', collapsed) || demandOf(byDock, 'right-bottom', collapsed);
  const bottomDemand = demandOf(byDock, 'bottom-left', collapsed) || demandOf(byDock, 'bottom-right', collapsed);

  // ---- Mobile fallback: everything folds into the center tab strip ----
  if (W <= cfg.mobileMaxW) {
    say(`width ${W} ≤ mobileMaxW ${cfg.mobileMaxW} → pure tabs`);
    const allTabs = [...center, ...DOCKS.flatMap((d) => byDock[d])];
    return {
      mode: 'tabs',
      viewport: { w: W, h: H },
      regions: [{
        id: 'center', kind: 'center', x: 0, y: 0, w: W, h: H,
        label: 'Center (tabs)', slots: [{ dockId: 'center', tabs: allTabs }], merged: false,
      }],
      gutters: [],
      overlays: [],
      decisions,
    };
  }

  // ---- WIDTH: side columns. capacity = how many side columns fit (rails are thin). ----
  let showLeft = false, showRight = false;
  const swapEdges = new Set();    // hidden side under mutual exclusion: click swaps which side shows
  const overlayEdges = new Set(); // no column fits at all: click peeks an overlay

  const capacity = (cfg.centerMinW + 2 * cfg.sideMinW <= W) ? 2
    : (cfg.centerMinW + cfg.sideMinW <= W) ? 1 : 0;

  if (leftDemand && rightDemand) {
    if (capacity >= 2) {
      showLeft = showRight = true;
    } else if (capacity === 1) {
      if (activeSide === 'right') { showRight = true; swapEdges.add('left'); }
      else { showLeft = true; swapEdges.add('right'); }
      say(`one side column fits → ${showLeft ? 'left' : 'right'} shown, other swappable from its gutter`);
    } else {
      overlayEdges.add('left'); overlayEdges.add('right');
      say('no room for any side column → sides peek as overlays');
    }
  } else if (leftDemand) {
    if (capacity >= 1) showLeft = true;
    else { overlayEdges.add('left'); say('no room for left column → overlay'); }
  } else if (rightDemand) {
    if (capacity >= 1) showRight = true;
    else { overlayEdges.add('right'); say('no room for right column → overlay'); }
  }

  // ---- WIDTH: bottom mode/span. The mode must be decided BEFORE gutters/rails are known,
  // so it runs on a PROVISIONAL center width (structural mins, full W — rails are thin). The
  // final geometry below recomputes widths with the resize clamps. ----
  const provLeftW = showLeft ? clamp(W * cfg.leftColFrac, cfg.sideMinW, W - cfg.centerMinW - (showRight ? cfg.sideMinW : 0)) : 0;
  const provRightW = showRight ? clamp(W * cfg.rightColFrac, cfg.sideMinW, W - cfg.centerMinW - provLeftW) : 0;
  const provCenterW = W - provLeftW - provRightW;

  let mode = 'widescreen';
  let bottomSpan = 'center';       // 'center' (under center) | 'full' (classic, full width)
  let mergeBottom = false;
  let bottomOverlay = false;

  if (bottomDemand) {
    const bL = demandOf(byDock, 'bottom-left', collapsed);
    const bR = demandOf(byDock, 'bottom-right', collapsed);
    const both = bL && bR;

    // WIDTH-ONLY: the bottom sits under the center until the center gets narrower than
    // a comfort threshold, then it flips to full width (classic). Independent of sibling
    // count and of column heights.
    if (provCenterW >= cfg.bottomComfortW) {
      mode = 'widescreen'; bottomSpan = 'center';
    } else {
      mode = 'classic'; bottomSpan = 'full';
      say(`bottom full width: center col ${Math.round(provCenterW)} < comfort ${cfg.bottomComfortW}`);
    }

    // Not enough height for center + bottom → the bottom collapses to its gutter.
    if (H < cfg.centerMinH + cfg.bottomMinH) {
      bottomOverlay = true;
      say(`height ${H} < center ${cfg.centerMinH} + bottom ${cfg.bottomMinH} → bottom overlay`);
    }
    // Keep the two bottom siblings split only while the span can hold both.
    if (!bottomOverlay && both) {
      const spanW = bottomSpan === 'full' ? W : provCenterW;
      if (spanW < cfg.bottomMergeBelowW) {
        mergeBottom = true;
        say(`bottom siblings merged: span ${Math.round(spanW)} < ${cfg.bottomMergeBelowW}`);
      }
    }
  }

  // ---- Gutters: which docks land in an edge gutter, and what a click does ----
  // action: 'swap' (show this side as the column) | 'overlay' (peek) | 'restore' (un-minimize)
  function responsiveAction(edge) {
    if (edge === 'bottom') return bottomOverlay ? 'overlay' : null;
    if (swapEdges.has(edge)) return 'swap';
    if (overlayEdges.has(edge)) return 'overlay';
    return null;
  }
  function gutterItems(edge) {
    const ra = responsiveAction(edge);
    const out = [];
    for (const d of EDGE_DOCKS[edge]) {
      if (!byDock[d].length) continue;
      let action;
      if (collapsed.has(d)) action = 'restore';
      else if (ra) action = ra;
      else continue; // shown inside a column / region, not in a gutter
      out.push({ dockId: d, tabs: byDock[d], action, anchor: DOCK_ANCHOR[d] });
    }
    return out;
  }
  const gutterData = {};
  for (const edge of ['left', 'right', 'bottom']) {
    const items = gutterItems(edge);
    if (items.length) gutterData[edge] = items;
  }

  // ---- Geometry. Gutters take REAL space, and each gutter's extent mirrors the region
  // it replaces: in widescreen the bottom (and its gutter) sits under the center only; in
  // classic the bottom is full width and the side columns/gutters span only the top band. ----
  const RAIL = cfg.railW;
  const leftGut = !!gutterData.left, rightGut = !!gutterData.right, bottomGut = !!gutterData.bottom;
  const fullBottom = (bottomSpan === 'full'); // classic owns the full width at the bottom

  // Vertical bands. A bottom region and an optional bottom rail stack (region above rail).
  const bottomShown = bottomDemand && !bottomOverlay;
  const bottomRailH = bottomGut ? RAIL : 0;
  const bottomRegionH = bottomShown ? clamp(H * cfg.bottomFrac, cfg.bottomResizeMinH, H - cfg.centerResizeMinH - bottomRailH) : 0;
  const bottomBandH = bottomRegionH + bottomRailH;
  const topBandH = H - bottomBandH;            // center height
  const sideBandH = fullBottom ? topBandH : H; // sides keep full height only in widescreen

  // Horizontal bands (top band): side rails reserve real width beside the columns.
  const railLeft = leftGut ? RAIL : 0;
  const railRight = rightGut ? RAIL : 0;
  const innerW = W - railLeft - railRight;
  // The actively-resized column keeps its desired width; the OTHER absorbs the squeeze
  // (down to its min) while the center holds at centerResizeMinW. Symmetric left/right:
  // the dragged column is computed first, so it wins. Releasing restores the other.
  const sideColWidth = (frac, reserveOther) => clamp(innerW * frac, cfg.sideResizeMinW, innerW - cfg.centerResizeMinW - reserveOther);
  let leftW = 0, rightW = 0;
  if (activeResize === 'right') {
    if (showRight) rightW = sideColWidth(cfg.rightColFrac, showLeft ? cfg.sideResizeMinW : 0);
    if (showLeft) leftW = sideColWidth(cfg.leftColFrac, rightW);
  } else {
    if (showLeft) leftW = sideColWidth(cfg.leftColFrac, showRight ? cfg.sideResizeMinW : 0);
    if (showRight) rightW = sideColWidth(cfg.rightColFrac, leftW);
  }
  const centerW = innerW - leftW - rightW;
  const leftSpace = railLeft + leftW;          // x where the center starts
  const rightSpace = railRight + rightW;

  // Side sibling merge uses the side column's own height.
  const sideAvailH = sideBandH;
  function sideMode(top, bot) {
    const dt = demandOf(byDock, top, collapsed), db = demandOf(byDock, bot, collapsed);
    if (dt && db) {
      if (sideAvailH >= cfg.sideMergeBelowH) return 'split';
      say(`${top}/${bot} merged: side height ${Math.round(sideAvailH)} < ${cfg.sideMergeBelowH}`);
      return 'merged';
    }
    return 'single';
  }
  const leftMode = showLeft ? sideMode('left-top', 'left-bottom') : null;
  const rightMode = showRight ? sideMode('right-top', 'right-bottom') : null;

  // ---- Build regions ----
  const regions = [];
  const splitters = [];
  const mkSlot = (dockId) => ({ dockId, tabs: byDock[dockId] });

  // Sibling splitter fraction = the START dock's share, bounded ONLY by siblingMaxFrac
  // (one sibling ≤ that share, the other keeps the rest). It is NOT clamped to any px min —
  // merging vs splitting is decided purely by the viewport extent vs the merge threshold.
  function clampSplit(frac) {
    return clamp(frac, 1 - cfg.siblingMaxFrac, cfg.siblingMaxFrac);
  }

  function pushSideColumn(edge, x, colW, m) {
    const top = `${edge}-top`, bot = `${edge}-bottom`;
    if (m === 'split') {
      const key = edge === 'left' ? 'leftSplitFrac' : 'rightSplitFrac';
      const topH = sideBandH * clampSplit(cfg[key]);
      regions.push({ id: top, kind: `col-${edge}`, x, y: 0, w: colW, h: topH, label: top, slots: [mkSlot(top)], merged: false });
      regions.push({ id: bot, kind: `col-${edge}`, x, y: topH, w: colW, h: sideBandH - topH, label: bot, slots: [mkSlot(bot)], merged: false });
      splitters.push({ id: `${edge}-split`, kind: 'sib', axis: 'h', x, y: topH, w: colW, h: 0, originX: x, originY: 0, from: 'start', extent: sideBandH, configKey: key, docks: [top, bot] });
    } else {
      const slots = m === 'merged' ? [mkSlot(top), mkSlot(bot)] : [mkSlot(demandOf(byDock, top, collapsed) ? top : bot)];
      regions.push({
        id: `${edge}-col`, kind: `col-${edge}`, x, y: 0, w: colW, h: sideBandH,
        label: m === 'merged' ? `${edge} (merged)` : `${edge}`,
        slots, merged: m === 'merged', mergedFrom: m === 'merged' ? [top, bot] : undefined,
      });
    }
  }
  if (showLeft) pushSideColumn('left', railLeft, leftW, leftMode);
  if (showRight) pushSideColumn('right', W - rightSpace, rightW, rightMode);

  regions.push({
    id: 'center', kind: 'center', x: leftSpace, y: 0, w: centerW, h: topBandH,
    label: 'Center (chat)', slots: [{ dockId: 'center', tabs: center }], merged: false,
  });

  if (bottomShown) {
    const bx = fullBottom ? 0 : leftSpace;
    const bw = fullBottom ? W : centerW;
    const by = H - bottomBandH;
    const bL = demandOf(byDock, 'bottom-left', collapsed);
    const bR = demandOf(byDock, 'bottom-right', collapsed);
    if (bL && bR && !mergeBottom) {
      const lw = bw * clampSplit(cfg.bottomSplitFrac);
      regions.push({ id: 'bottom-left', kind: 'bottom', x: bx, y: by, w: lw, h: bottomRegionH, label: 'bottom-left', slots: [mkSlot('bottom-left')], merged: false });
      regions.push({ id: 'bottom-right', kind: 'bottom', x: bx + lw, y: by, w: bw - lw, h: bottomRegionH, label: 'bottom-right', slots: [mkSlot('bottom-right')], merged: false });
      splitters.push({ id: 'bottom-split', kind: 'sib', axis: 'v', x: bx + lw, y: by, w: 0, h: bottomRegionH, originX: bx, originY: by, from: 'start', extent: bw, configKey: 'bottomSplitFrac', docks: ['bottom-left', 'bottom-right'] });
    } else {
      const slots = mergeBottom ? [mkSlot('bottom-left'), mkSlot('bottom-right')] : [mkSlot(bL ? 'bottom-left' : 'bottom-right')];
      regions.push({
        id: 'bottom', kind: 'bottom', x: bx, y: by, w: bw, h: bottomRegionH,
        label: mergeBottom ? 'bottom (merged)' : 'bottom',
        slots, merged: mergeBottom, mergedFrom: mergeBottom ? ['bottom-left', 'bottom-right'] : undefined,
      });
    }
  }

  // ---- Dock-size splitters: drag a column's width or the bottom's height vs the center ----
  // `docks` = the shown docks of the zone the splitter resizes (the whole side column / bottom band
  // for 'dock' splitters, the two siblings for 'sib' ones) — lets the renderer map a splitter
  // double-click to a maximize target without re-deriving layout rules from splitter ids.
  const shownEdgeDocks = (edge) => EDGE_DOCKS[edge].filter((d) => demandOf(byDock, d, collapsed));
  if (showLeft) splitters.push({ id: 'left-dock', kind: 'dock', axis: 'v', x: leftSpace, y: 0, w: 0, h: sideBandH, originX: railLeft, originY: 0, from: 'start', extent: innerW, configKey: 'leftColFrac', docks: shownEdgeDocks('left') });
  if (showRight) splitters.push({ id: 'right-dock', kind: 'dock', axis: 'v', x: W - rightSpace, y: 0, w: 0, h: sideBandH, originX: railLeft + innerW, originY: 0, from: 'end', extent: innerW, configKey: 'rightColFrac', docks: shownEdgeDocks('right') });
  if (bottomShown) splitters.push({ id: 'bottom-dock', kind: 'dock', axis: 'h', x: fullBottom ? 0 : leftSpace, y: H - bottomBandH, w: fullBottom ? W : centerW, h: 0, originX: 0, originY: H, from: 'end', extent: H, configKey: 'bottomFrac', docks: shownEdgeDocks('bottom') });

  // ---- Gutter rects (extent mirrors the region, per mode) + overlay rects ----
  const cov = cfg.overlayCoverage;
  const railRect = {
    left: { x: 0, y: 0, w: RAIL, h: sideBandH },
    right: { x: W - RAIL, y: 0, w: RAIL, h: sideBandH },
    bottom: fullBottom
      ? { x: 0, y: H - bottomRailH, w: W, h: bottomRailH }
      : { x: leftSpace, y: H - bottomRailH, w: centerW, h: bottomRailH },
  };
  const cW = innerW, cH = H - bottomRailH;
  const overlayRect = {
    left: { x: railLeft, y: 0, w: cW * cov, h: cH },
    right: { x: railLeft + cW * (1 - cov), y: 0, w: cW * cov, h: cH },
    bottom: { x: railLeft, y: cH * (1 - cov), w: cW, h: cH * cov },
  };
  const gutters = [];
  const overlays = [];
  for (const edge of ['left', 'right', 'bottom']) {
    const items = gutterData[edge];
    if (!items) continue;
    gutters.push({ edge, ...railRect[edge], items });
    if (items.some((it) => it.action === 'overlay')) {
      overlays.push({ edge, rect: overlayRect[edge], tabs: edgeTabs(byDock, edge) });
    }
  }

  return { mode, viewport: { w: W, h: H }, regions, gutters, overlays, splitters, decisions };
}
