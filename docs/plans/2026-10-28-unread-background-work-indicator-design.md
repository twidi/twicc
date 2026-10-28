# Unread session with background work: alternating indicator

## Problem

A session in `user_turn` can be unread and still have background work behind the finished turn:
a background shell, or an active cron. Today the unread eye replaces every other indicator, so these
two cases look the same:

- the agent finished and there is something to read;
- the agent finished its turn, but a shell or cron still runs, so more output may come.

The user cannot tell whether to go read now or to wait.

## Goal

An unread session whose turn is over, with background work still behind it, alternates between the
unread eye and the background-work icon. The eye alone keeps its current meaning: something is
really finished, go read it.

The aggregated indicators (project, workspace, collapsed tree row) follow the same meaning:
the eye alone wins as soon as one unread session has no background work.

## Non-goals

- No change to how "unread" is computed (`isSessionUnread`).
- No change to the read-session icons (terminal, clock, check).
- No change to tooltips. They already name unread sessions, shells and crons.
- No change to the command palette. It already shows the process indicator before the unread eye
  (the eye appears only for a row without a live process), and its workspace and project rows use
  their own aggregation with a boolean unread flag. It keeps its own precedence.
- No change to the favicon and the global unread count. They signal any unread session and do not
  carry the "unread with background work" distinction.
- No change to the existing precedence of the pending-request hand on each surface (some surfaces
  show the hand and the eye together, others the eye only). This spec only decides between the
  eye alone and the eye alternating with background work.
- No CHANGELOG entry (on explicit request only).

## Behaviour

### One session

Background work = at least one background shell, or at least one active cron, while the session is
in `user_turn`. A shell wins over a cron when both exist, as for a read session today.

| Session | Indicator |
|---|---|
| Unread, no background work | Eye (unchanged) |
| Unread, background shell | Eye alternating with the terminal icon |
| Unread, active cron, no shell | Eye alternating with the clock icon |
| Read | Unchanged |

A session that is not in `user_turn` is never unread (existing rule), so the alternation only
concerns `user_turn`.

### Aggregated (set of sessions)

Let *U* be the unread sessions of the set. Let *UB* be those of *U* that have background work.

The priority cascade becomes:

1. pending request
2. unread: *U* has at least one session **not** in *UB* (eye alone)
3. unread with background work: *U* is non-empty and every session of *U* is in *UB*
   (eye alternating with the background-work icon; terminal if any session of *UB* has a shell,
   else clock)
4. assistant turn
5. background shells
6. crons
7. active process
8. nothing

Step 3 is a new display mode. Everything else keeps its position.

### Animation

- The two icons alternate in a 2.4 s cycle: 1.2 s per icon, cross-fade.
- Opacity only, like the existing unread pulse. It stays active under reduced motion.
- The eye keeps its current colour. The background-work icon keeps its current colour.
- During the alternation, the cross-fade is the only animation. The per-icon pulses (the eye's
  breathing, the terminal icon's breathing) do not run on top of it. An icon shown alone
  keeps its current animation.

## Design decisions

- **`ProcessIndicator` owns the unread display.** It gets an `unread` input and renders the eye
  alone or the alternation. The per-session surfaces that draw the eye by hand today
  (session list rows in all their variants, including the compact row; the sidebar rail session
  rows and their child rows; the session switcher) render it through `ProcessIndicator`.
  This removes the duplicated "unread replaces process indicator" branches, and one component
  then decides what the user sees.
- **Per-surface styling is kept.** Each surface keeps its size, colour and layout. The unification
  covers the choice of icon and the animation, not the layout.
- **The aggregate carries two new figures, both scoped to *UB*** (not to the whole set): how many
  unread sessions have background work, and whether any of them has a shell. The icon of step 3
  comes from the second figure. The set-wide shell count is not enough: a read session with a
  shell must not turn the icon of an unread, cron-only session into a terminal.
  It is produced wherever the aggregate unread count is produced today (project and workspace
  badges, collapsed tree rows in the session list and in the rail).
  Every place that compares summaries to decide whether to refresh must treat the new count as
  part of the summary, so a change of this count refreshes the indicator.
- **Consistency**: for the same set of unread sessions *U*, every surface in scope makes the same
  choice between the eye alone and the alternation. Each surface keeps its existing rules that
  decide which sessions enter *U* (see "Behaviour to keep").
- **Recurring crons are accepted as background work.** An unread session with a standing cron
  alternates, and never shows the plain eye while the cron lives. This is intended: the
  session may produce more output.
- **Orchestration tab**: its indicator aggregates active descendants and has no unread count.
  It is unchanged.

## Behaviour to keep

- A muted, hidden, draft, ephemeral, archived or child session is never unread. It never
  enters *U*.
- The currently viewed session is never counted as unread in a tree aggregate (existing rule).
- Startup: the project and workspace aggregates compute no unread while the initial sync runs
  (existing rule, not extended to other surfaces).

## Tests

- Aggregate cascade: the three unread situations (pure unread present; all unread with work; no
  unread) and the shell-over-cron choice for the icon.
- Per-session: each row of the single-session table.
- Style/animation: the alternation needs new coverage (the cross-fade and its reduced-motion
  behaviour). The existing animation checks in `glow.test.js` pin the unread eye's pulse at named
  sites, so they need updating where the hand-drawn eyes go away.

## Open points

None.
