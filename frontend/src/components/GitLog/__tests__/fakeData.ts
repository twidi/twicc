import type { GitLogEntry, GitLogIndexStatus } from '../types'

// ---------------------------------------------------------------------------
// Authors
// ---------------------------------------------------------------------------

const authors = {
  alice: { name: 'Alice Martin', email: 'alice.martin@example.com' },
  bob: { name: 'Bob Chen', email: 'bob.chen@example.com' },
  clara: { name: 'Clara Santos', email: 'clara.santos@example.com' },
  david: { name: 'David Kim', email: 'david.kim@example.com' },
} as const

// ---------------------------------------------------------------------------
// Helper — ISO date generator (dates spread over ~6 weeks)
// ---------------------------------------------------------------------------

/**
 * Generates an ISO-8601 date string offset by the given number of hours
 * from a fixed base date (2026-01-05T09:00:00Z).
 */
function date(hoursFromBase: number): string {
  const base = new Date('2026-01-05T09:00:00Z')
  base.setTime(base.getTime() + hoursFromBase * 3600_000)
  return base.toISOString()
}

// ---------------------------------------------------------------------------
// Fake git log entries
//
// Topology overview (newest at top, oldest at bottom):
//
//   main ─────────────────────────────────────────────────────────────────
//   feature/auth ─────────┐ (merged into main at d9d0e1f)
//   fix/login-bug ────┐   │ (merged into main at d6a7b8c)
//   feature/dashboard ┐   │   │ (merged into main at b2c3d4e)
//   origin/feature/notifications (remote, unmerged, concurrent)
//
// Key moments:
//   - 4 branches coexist around hours 820–960 (main, dashboard,
//     notifications, and either fix/login-bug or auth still visible)
//   - 3 merge commits: feature/auth, fix/login-bug, feature/dashboard
//   - 3 tags: v1.0.0 (release), v1.1.0 (merge fix), v2.0.0-beta (merge dashboard)
//   - 4 different authors
//   - 41 commits total
//
// Order: strictly descending by committerDate (like `git log`).
// ---------------------------------------------------------------------------

export const fakeEntries: GitLogEntry[] = [
  // ── Hour 1008 — HEAD of main ───────────────────────────────────────
  {
    hash: 'a1b2c3d',
    branch: 'refs/heads/main',
    parents: ['b2c3d4e'],
    message: 'docs: update changelog for v2.0.0-beta',
    author: authors.alice,
    committerDate: date(1008),
    authorDate: date(1008),
  },

  // ── Hour 1000 — Merge feature/dashboard into main, tagged v2.0.0-beta
  {
    hash: 'b2c3d4e',
    branch: 'refs/tags/v2.0.0-beta',
    parents: ['c3d4e5f', 'f1a2b3c'],
    message: 'Merge branch \'feature/dashboard\' into main',
    author: authors.alice,
    committerDate: date(1000),
    authorDate: date(1000),
  },

  // ── Hour 980 — feature/dashboard ─────────────────────────────────────
  {
    hash: 'f1a2b3c',
    branch: 'refs/heads/feature/dashboard',
    parents: ['f2b3c4d'],
    message: 'feat(dashboard): add real-time data refresh',
    author: authors.clara,
    committerDate: date(980),
    authorDate: date(980),
  },

  // ── Hour 960 — feature/dashboard ─────────────────────────────────────
  {
    hash: 'f2b3c4d',
    branch: 'refs/heads/feature/dashboard',
    parents: ['f3c4d5e'],
    message: 'feat(dashboard): implement widget drag-and-drop',
    author: authors.clara,
    committerDate: date(960),
    authorDate: date(960),
  },

  // ── Hour 950 — origin/feature/notifications (concurrent) ─────────────
  {
    hash: 'e1f2a3b',
    branch: 'refs/remotes/origin/feature/notifications',
    parents: ['e2a3b4c'],
    message: 'feat(notifications): add push notification support',
    author: authors.david,
    committerDate: date(950),
    authorDate: date(950),
  },

  // ── Hour 920 — feature/dashboard ─────────────────────────────────────
  {
    hash: 'f3c4d5e',
    branch: 'refs/heads/feature/dashboard',
    parents: ['f4d5e6f'],
    message: 'feat(dashboard): add chart rendering with D3',
    author: authors.david,
    committerDate: date(920),
    authorDate: date(920),
  },

  // ── Hour 900 — origin/feature/notifications (concurrent) ─────────────
  {
    hash: 'e2a3b4c',
    branch: 'refs/remotes/origin/feature/notifications',
    parents: ['e3b4c5d'],
    message: 'feat(notifications): implement toast component',
    author: authors.david,
    committerDate: date(900),
    authorDate: date(900),
  },

  // ── Hour 880 — feature/dashboard ─────────────────────────────────────
  {
    hash: 'f4d5e6f',
    branch: 'refs/heads/feature/dashboard',
    parents: ['f5e6f7a'],
    message: 'feat(dashboard): create layout grid component',
    author: authors.clara,
    committerDate: date(880),
    authorDate: date(880),
  },

  // ── Hour 860 — origin/feature/notifications (concurrent) ─────────────
  {
    hash: 'e3b4c5d',
    branch: 'refs/remotes/origin/feature/notifications',
    parents: ['e4c5d6e'],
    message: 'feat(notifications): create notification service',
    author: authors.david,
    committerDate: date(860),
    authorDate: date(860),
  },

  // ── Hour 840 — feature/dashboard (first commit, forks from main) ─────
  {
    hash: 'f5e6f7a',
    branch: 'refs/heads/feature/dashboard',
    parents: ['c3d4e5f'],
    message: 'feat(dashboard): scaffold dashboard page and routes',
    author: authors.clara,
    committerDate: date(840),
    authorDate: date(840),
  },

  // ── Hour 830 — main ──────────────────────────────────────────────────
  {
    hash: 'c3d4e5f',
    branch: 'refs/heads/main',
    parents: ['d4e5f6a'],
    message: 'chore: upgrade TypeScript to 5.7',
    author: authors.bob,
    committerDate: date(830),
    authorDate: date(830),
  },

  // ── Hour 820 — origin/feature/notifications (first, forks from main) ─
  {
    hash: 'e4c5d6e',
    branch: 'refs/remotes/origin/feature/notifications',
    parents: ['d4e5f6a'],
    message: 'feat(notifications): scaffold notification module',
    author: authors.david,
    committerDate: date(820),
    authorDate: date(820),
  },

  // ── Hour 800 — main ──────────────────────────────────────────────────
  {
    hash: 'd4e5f6a',
    branch: 'refs/heads/main',
    parents: ['d5f6a7b'],
    message: 'ci: add GitHub Actions workflow for deployment',
    author: authors.alice,
    committerDate: date(800),
    authorDate: date(800),
  },

  // ── Hour 760 — main ──────────────────────────────────────────────────
  {
    hash: 'd5f6a7b',
    branch: 'refs/heads/main',
    parents: ['d6a7b8c'],
    message: 'refactor: extract shared validation utilities',
    author: authors.bob,
    committerDate: date(760),
    authorDate: date(760),
  },

  // ── Hour 720 — Merge fix/login-bug into main, tagged v1.1.0 ─────────
  {
    hash: 'd6a7b8c',
    branch: 'refs/tags/v1.1.0',
    parents: ['d7b8c9d', 'g1a2b3c'],
    message: 'Merge branch \'fix/login-bug\' into main',
    author: authors.alice,
    committerDate: date(720),
    authorDate: date(720),
  },

  // ── Hour 710 — fix/login-bug ─────────────────────────────────────────
  {
    hash: 'g1a2b3c',
    branch: 'refs/heads/fix/login-bug',
    parents: ['g2b3c4d'],
    message: 'fix(auth): handle expired session token gracefully',
    author: authors.bob,
    committerDate: date(710),
    authorDate: date(710),
  },

  // ── Hour 690 — fix/login-bug ─────────────────────────────────────────
  {
    hash: 'g2b3c4d',
    branch: 'refs/heads/fix/login-bug',
    parents: ['g3c4d5e'],
    message: 'fix(auth): correct password validation regex',
    author: authors.bob,
    committerDate: date(690),
    authorDate: date(690),
  },

  // ── Hour 670 — fix/login-bug (first commit, forks from main) ────────
  {
    hash: 'g3c4d5e',
    branch: 'refs/heads/fix/login-bug',
    parents: ['d7b8c9d'],
    message: 'test(auth): add regression tests for login flow',
    author: authors.bob,
    committerDate: date(670),
    authorDate: date(670),
  },

  // ── Hour 650 — main ──────────────────────────────────────────────────
  {
    hash: 'd7b8c9d',
    branch: 'refs/heads/main',
    parents: ['d8c9d0e'],
    message: 'style: apply consistent code formatting with Prettier',
    author: authors.alice,
    committerDate: date(650),
    authorDate: date(650),
  },

  // ── Hour 620 — main ──────────────────────────────────────────────────
  {
    hash: 'd8c9d0e',
    branch: 'refs/heads/main',
    parents: ['d9d0e1f'],
    message: 'chore: update dependency lock file',
    author: authors.alice,
    committerDate: date(620),
    authorDate: date(620),
  },

  // ── Hour 600 — Merge feature/auth into main ──────────────────────────
  {
    hash: 'd9d0e1f',
    branch: 'refs/heads/main',
    parents: ['e0e1f2a', 'h1a2b3c'],
    message: 'Merge branch \'feature/auth\' into main',
    author: authors.alice,
    committerDate: date(600),
    authorDate: date(600),
  },

  // ── Hour 590 — feature/auth ──────────────────────────────────────────
  {
    hash: 'h1a2b3c',
    branch: 'refs/heads/feature/auth',
    parents: ['h2b3c4d'],
    message: 'feat(auth): add password strength indicator',
    author: authors.clara,
    committerDate: date(590),
    authorDate: date(590),
  },

  // ── Hour 560 — feature/auth ──────────────────────────────────────────
  {
    hash: 'h2b3c4d',
    branch: 'refs/heads/feature/auth',
    parents: ['h3c4d5e'],
    message: 'feat(auth): implement JWT token refresh',
    author: authors.clara,
    committerDate: date(560),
    authorDate: date(560),
  },

  // ── Hour 530 — feature/auth ──────────────────────────────────────────
  {
    hash: 'h3c4d5e',
    branch: 'refs/heads/feature/auth',
    parents: ['h4d5e6f'],
    message: 'feat(auth): add OAuth2 provider integration',
    author: authors.david,
    committerDate: date(530),
    authorDate: date(530),
  },

  // ── Hour 500 — feature/auth ──────────────────────────────────────────
  {
    hash: 'h4d5e6f',
    branch: 'refs/heads/feature/auth',
    parents: ['h5e6f7a'],
    message: 'feat(auth): create login and registration forms',
    author: authors.clara,
    committerDate: date(500),
    authorDate: date(500),
  },

  // ── Hour 470 — feature/auth ──────────────────────────────────────────
  {
    hash: 'h5e6f7a',
    branch: 'refs/heads/feature/auth',
    parents: ['h6f7a8b'],
    message: 'feat(auth): set up authentication middleware',
    author: authors.david,
    committerDate: date(470),
    authorDate: date(470),
  },

  // ── Hour 440 — feature/auth (first commit, forks from main) ──────────
  {
    hash: 'h6f7a8b',
    branch: 'refs/heads/feature/auth',
    parents: ['e0e1f2a'],
    message: 'feat(auth): scaffold authentication module',
    author: authors.clara,
    committerDate: date(440),
    authorDate: date(440),
  },

  // ── Hour 420 — main ──────────────────────────────────────────────────
  {
    hash: 'e0e1f2a',
    branch: 'refs/heads/main',
    parents: ['e1f2a3c'],
    message: 'docs: add API documentation for REST endpoints',
    author: authors.alice,
    committerDate: date(420),
    authorDate: date(420),
  },

  // ── Hour 380 — main ──────────────────────────────────────────────────
  {
    hash: 'e1f2a3c',
    branch: 'refs/heads/main',
    parents: ['e2a3c4d'],
    message: 'feat: add error boundary and fallback UI',
    author: authors.bob,
    committerDate: date(380),
    authorDate: date(380),
  },

  // ── Hour 350 — main, tagged v1.0.0 ───────────────────────────────────
  {
    hash: 'e2a3c4d',
    branch: 'refs/tags/v1.0.0',
    parents: ['e3c4d5f'],
    message: 'release: v1.0.0',
    author: authors.alice,
    committerDate: date(350),
    authorDate: date(350),
  },

  // ── Hour 320 — main ──────────────────────────────────────────────────
  {
    hash: 'e3c4d5f',
    branch: 'refs/heads/main',
    parents: ['e4d5f6a'],
    message: 'feat: implement global search functionality',
    author: authors.bob,
    committerDate: date(320),
    authorDate: date(320),
  },

  // ── Hour 290 — main ──────────────────────────────────────────────────
  {
    hash: 'e4d5f6a',
    branch: 'refs/heads/main',
    parents: ['e5f6a7b'],
    message: 'feat: add responsive navigation sidebar',
    author: authors.clara,
    committerDate: date(290),
    authorDate: date(290),
  },

  // ── Hour 260 — main ──────────────────────────────────────────────────
  {
    hash: 'e5f6a7b',
    branch: 'refs/heads/main',
    parents: ['e6a7b8c'],
    message: 'refactor: migrate state management to Pinia',
    author: authors.bob,
    committerDate: date(260),
    authorDate: date(260),
  },

  // ── Hour 230 — main ──────────────────────────────────────────────────
  {
    hash: 'e6a7b8c',
    branch: 'refs/heads/main',
    parents: ['e7b8c9d'],
    message: 'feat: create user profile page',
    author: authors.david,
    committerDate: date(230),
    authorDate: date(230),
  },

  // ── Hour 200 — main ──────────────────────────────────────────────────
  {
    hash: 'e7b8c9d',
    branch: 'refs/heads/main',
    parents: ['e8c9d0e'],
    message: 'chore: configure ESLint and Prettier',
    author: authors.alice,
    committerDate: date(200),
    authorDate: date(200),
  },

  // ── Hour 170 — main ──────────────────────────────────────────────────
  {
    hash: 'e8c9d0e',
    branch: 'refs/heads/main',
    parents: ['e9d0e1f'],
    message: 'feat: set up project routing with Vue Router',
    author: authors.bob,
    committerDate: date(170),
    authorDate: date(170),
  },

  // ── Hour 140 — main ──────────────────────────────────────────────────
  {
    hash: 'e9d0e1f',
    branch: 'refs/heads/main',
    parents: ['f0e1f2a'],
    message: 'feat: add API client with Axios interceptors',
    author: authors.david,
    committerDate: date(140),
    authorDate: date(140),
  },

  // ── Hour 110 — main ──────────────────────────────────────────────────
  {
    hash: 'f0e1f2a',
    branch: 'refs/heads/main',
    parents: ['f1f2a3d'],
    message: 'chore: configure Vite build and dev server',
    author: authors.alice,
    committerDate: date(110),
    authorDate: date(110),
  },

  // ── Hour 80 — main ───────────────────────────────────────────────────
  {
    hash: 'f1f2a3d',
    branch: 'refs/heads/main',
    parents: ['f2a3d4e'],
    message: 'feat: create base layout with header and footer',
    author: authors.clara,
    committerDate: date(80),
    authorDate: date(80),
  },

  // ── Hour 50 — main ───────────────────────────────────────────────────
  {
    hash: 'f2a3d4e',
    branch: 'refs/heads/main',
    parents: ['f3d4e5a'],
    message: 'chore: initialize Vue 3 project with TypeScript',
    author: authors.alice,
    committerDate: date(50),
    authorDate: date(50),
  },

  // ── Hour 0 — main (initial commit) ───────────────────────────────────
  {
    hash: 'f3d4e5a',
    branch: 'refs/heads/main',
    parents: [],
    message: 'Initial commit',
    author: authors.alice,
    committerDate: date(0),
    authorDate: date(0),
  },
]

// ---------------------------------------------------------------------------
// Fake index status (working directory changes)
// ---------------------------------------------------------------------------

export const fakeIndexStatus: GitLogIndexStatus = {
  modified: 3,
  added: 1,
  deleted: 0,
}
