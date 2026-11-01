# Telemetry Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Cloudflare Worker + D1 collector that receives TwiCC's anonymous telemetry payloads and serves the public transparency page, as a self-contained `telemetry-collector/` subfolder of the TwiCC repo.

**Architecture:** One Worker bound to one D1 (SQLite) database, attached to the custom domain `twicc-telemetry.twidi.com`. Static assets serve `GET /` (transparency page); the worker code handles only `POST /v1/telemetry` (validate → insert raw payload → 204). Insert-only, no IP persistence, no auth. Design: `docs/plans/2026-07-18-telemetry-design.md` §7 (fixed decisions in §2; payload shape in §3).

**Tech Stack:** Cloudflare Workers (plain JS ES modules, no TS/build step), D1, `wrangler` CLI, `vitest` + `@cloudflare/vitest-pool-workers` for request-level tests.

**Not in this plan:** the TwiCC-side client (separate plan), any stats/reading interface (out of scope v1, design §9), CHANGELOG entries.

**Conventions:** everything in English. Commits follow the repo's Conventional Commits rules (descriptive body + `Co-Authored-By` trailer for the model actually running — see repo `CLAUDE.md`). `git add` only the listed files, never `-A`.

---

## File structure

```
telemetry-collector/
  README.md                 # operational guide (deploy, update, query, domain) — Task 4
  package.json              # pinned dev deps + npm scripts
  package-lock.json         # committed
  .gitignore                # node_modules/, .wrangler/
  wrangler.toml             # worker config: D1 binding, assets dir, custom domain
  vitest.config.js          # workers pool + D1 migrations applied to the test DB
  migrations/
    0001_payloads.sql       # the single table
  src/
    worker.js               # the whole worker (~40 lines)
  public/
    index.html              # transparency page (single file, inline CSS)
  test/
    apply-migrations.js     # vitest setup file
    worker.test.js          # request-level tests via SELF.fetch
```

The root `.gitignore` of the TwiCC repo is not touched; the subfolder has its own.

---

### Task 1: Scaffold the project

**Files:**
- Create: `telemetry-collector/package.json`
- Create: `telemetry-collector/.gitignore`
- Create: `telemetry-collector/wrangler.toml`
- Create: `telemetry-collector/public/index.html` (stub)
- Create: `telemetry-collector/migrations/0001_payloads.sql`

- [ ] **Step 1: Create the folder and `.gitignore`**

`telemetry-collector/.gitignore`:

```gitignore
node_modules/
.wrangler/
```

- [ ] **Step 2: Create `package.json`**

```json
{
  "name": "twicc-telemetry-collector",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy",
    "test": "vitest run",
    "tail": "wrangler tail",
    "db:migrate:remote": "wrangler d1 migrations apply twicc-telemetry --remote"
  }
}
```

- [ ] **Step 3: Install dev dependencies**

Run (inside `telemetry-collector/`):

```bash
npm install -D wrangler @cloudflare/vitest-pool-workers vitest
```

Note: `@cloudflare/vitest-pool-workers` pins a compatible `vitest` peer range; if npm reports a peer conflict, install the vitest version its error message asks for. This is the only version-sensitive pairing in the project.

Expected: `package-lock.json` created, `node_modules/` present (ignored).

- [ ] **Step 4: Create `wrangler.toml`**

```toml
name = "twicc-telemetry"
main = "src/worker.js"
compatibility_date = "2026-07-01"

# Transparency page: files in ./public are served automatically for matching
# GET requests; everything else falls through to the worker code.
[assets]
directory = "./public"

# Custom domain — wrangler creates the DNS record + certificate on deploy
# (the twidi.com zone is already on this Cloudflare account).
routes = [
  { pattern = "twicc-telemetry.twidi.com", custom_domain = true }
]

[[d1_databases]]
binding = "DB"
database_name = "twicc-telemetry"
database_id = "REPLACE_AFTER_D1_CREATE"   # filled in Task 6 (first deploy)
migrations_dir = "migrations"
```

The placeholder `database_id` is fine for local dev and tests (both use a local simulated D1 keyed by the binding); only `deploy`/`--remote` commands need the real id.

- [ ] **Step 5: Create a stub for the assets directory**

`wrangler.toml` declares `[assets] directory = "./public"`, and wrangler (including the vitest workers pool, which reads the same config) errors if that directory does not exist — so Task 2's test run needs it present before the real page exists. Create `telemetry-collector/public/index.html` as a stub (replaced wholesale in Task 3):

```html
<!doctype html><title>TwiCC telemetry</title>
```

- [ ] **Step 6: Create the migration**

`telemetry-collector/migrations/0001_payloads.sql`:

```sql
-- One row per received telemetry payload. Raw JSON is the source of truth
-- (design: the reading side evolves without redeploying the collector);
-- schema + instance_id are extracted as columns for cheap querying.
-- No IP, no user agent, no request metadata of any kind is stored.
CREATE TABLE payloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    schema INTEGER NOT NULL,
    instance_id TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE INDEX idx_payloads_instance ON payloads(instance_id);
CREATE INDEX idx_payloads_received ON payloads(received_at);
```

- [ ] **Step 7: Commit**

```bash
git add telemetry-collector/package.json telemetry-collector/package-lock.json telemetry-collector/.gitignore telemetry-collector/wrangler.toml telemetry-collector/public/index.html telemetry-collector/migrations/0001_payloads.sql
git commit -m "feat(telemetry): scaffold the Cloudflare collector project"
```

(Descriptive body + co-author trailer per repo conventions — applies to every commit below, not repeated.)

---

### Task 2: Worker — tests first, then implementation

**Files:**
- Create: `telemetry-collector/vitest.config.js`
- Create: `telemetry-collector/test/apply-migrations.js`
- Create: `telemetry-collector/test/worker.test.js`
- Create: `telemetry-collector/src/worker.js`

- [ ] **Step 1: Create the vitest config (workers pool + migrations)**

`telemetry-collector/vitest.config.js`:

```js
import { fileURLToPath } from "node:url";
import { defineWorkersConfig, readD1Migrations } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig(async () => {
  // fileURLToPath keeps this working on Node 18/20 alike
  // (import.meta.dirname only exists from Node 20.11).
  const migrations = await readD1Migrations(fileURLToPath(new URL("migrations/", import.meta.url)));
  return {
    test: {
      setupFiles: ["./test/apply-migrations.js"],
      poolOptions: {
        workers: {
          wrangler: { configPath: "./wrangler.toml" },
          miniflare: {
            bindings: { TEST_MIGRATIONS: migrations },
          },
        },
      },
    },
  };
});
```

`telemetry-collector/test/apply-migrations.js`:

```js
import { applyD1Migrations, env } from "cloudflare:test";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
```

- [ ] **Step 2: Write the failing tests**

`telemetry-collector/test/worker.test.js`:

```js
import { describe, it, expect } from "vitest";
import { SELF, env } from "cloudflare:test";

const ENDPOINT = "https://collector.test/v1/telemetry";

function validPayload(overrides = {}) {
  return {
    schema: 1,
    instance_id: "3b1e9c2a-0000-4000-8000-1234567890ab",
    instance: { twicc_version: "1.0.0" },
    days: [],
    ...overrides,
  };
}

async function post(body) {
  return SELF.fetch(ENDPOINT, { method: "POST", body });
}

describe("POST /v1/telemetry", () => {
  it("stores a valid payload and returns 204", async () => {
    const body = JSON.stringify(validPayload());
    const res = await post(body);
    expect(res.status).toBe(204);

    const { results } = await env.DB.prepare(
      "SELECT schema, instance_id, body FROM payloads"
    ).all();
    expect(results).toHaveLength(1);
    expect(results[0].schema).toBe(1);
    expect(results[0].instance_id).toBe("3b1e9c2a-0000-4000-8000-1234567890ab");
    expect(results[0].body).toBe(body);
  });

  it("rejects invalid JSON with 400 and stores nothing", async () => {
    const res = await post("{not json");
    expect(res.status).toBe(400);
    const { results } = await env.DB.prepare("SELECT id FROM payloads").all();
    expect(results).toHaveLength(0);
  });

  it("rejects an unsupported schema version with 400", async () => {
    const res = await post(JSON.stringify(validPayload({ schema: 999 })));
    expect(res.status).toBe(400);
  });

  it("rejects a missing instance_id with 400", async () => {
    const res = await post(JSON.stringify(validPayload({ instance_id: undefined })));
    expect(res.status).toBe(400);
  });

  it("rejects an oversized body with 413", async () => {
    const res = await post(JSON.stringify(validPayload({ padding: "x".repeat(70_000) })));
    expect(res.status).toBe(413);
  });

  it("rejects non-POST methods with 405", async () => {
    const res = await SELF.fetch(ENDPOINT, { method: "GET" });
    expect(res.status).toBe(405);
    expect(res.headers.get("Allow")).toBe("POST");
  });
});

describe("other paths", () => {
  it("returns 404 for unknown paths", async () => {
    const res = await SELF.fetch("https://collector.test/nope", { method: "POST" });
    expect(res.status).toBe(404);
  });
});
```

Note: `GET /` (the transparency page) is served by the assets layer and is checked manually with `wrangler dev` in Task 3, not unit-tested here.

- [ ] **Step 3: Run tests to verify they fail**

Run (inside `telemetry-collector/`): `npm test`
Expected: FAIL — `src/worker.js` does not exist / module not found.

- [ ] **Step 4: Implement the worker**

`telemetry-collector/src/worker.js`:

```js
// TwiCC telemetry collector. Insert-only by design: validates the envelope,
// stores the raw payload, answers 204. Never reads, logs, or stores the
// client IP or any request metadata (a stated public commitment — see
// docs/plans/2026-07-18-telemetry-design.md §4/§7 in the TwiCC repo).

// Rough cap on payload size. `String.length` counts UTF-16 code units, not
// bytes — close enough for an abuse guard on ASCII-heavy JSON.
const MAX_BODY_CHARS = 64_000;

const SUPPORTED_SCHEMAS = new Set([1]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/v1/telemetry") {
      if (request.method !== "POST") {
        return new Response(null, { status: 405, headers: { Allow: "POST" } });
      }
      return handleTelemetry(request, env);
    }

    // Static assets (the transparency page) are served before the worker
    // runs; anything reaching this point is an unknown path.
    return new Response(null, { status: 404 });
  },
};

async function handleTelemetry(request, env) {
  const body = await request.text();
  if (body.length > MAX_BODY_CHARS) {
    return new Response(null, { status: 413 });
  }

  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return new Response(null, { status: 400 });
  }

  if (!SUPPORTED_SCHEMAS.has(payload?.schema) || typeof payload?.instance_id !== "string") {
    return new Response(null, { status: 400 });
  }

  await env.DB.prepare("INSERT INTO payloads (schema, instance_id, body) VALUES (?1, ?2, ?3)")
    .bind(payload.schema, payload.instance_id, body)
    .run();

  return new Response(null, { status: 204 });
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm test`
Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add telemetry-collector/vitest.config.js telemetry-collector/test/apply-migrations.js telemetry-collector/test/worker.test.js telemetry-collector/src/worker.js
git commit -m "feat(telemetry): collector worker with ingestion endpoint and tests"
```

---

### Task 3: Transparency page

**Files:**
- Create: `telemetry-collector/public/index.html`

- [ ] **Step 1: Write the page**

Single self-contained HTML file, inline CSS, sober adult register (no emojis, no hand-holding). Content requirements — the page is the public reference the TwiCC settings toggle links to (design §4/§7), so it must cover, in this order:

1. **What this is** — one short paragraph: TwiCC installs send anonymous usage statistics here, enabled by default, disabled in one click.
2. **What is collected** — a field-by-field table matching design §3.1/§3.2 exactly: instance block (version, OS/arch, Python, providers, install method, bucketed project/workspace counts, remote-access boolean) and daily blocks (session counts per model family / effort / permission mode, message counts, subagents, workflows, crons, presence bucket, peak concurrent agents, cost bucket, feature counters).
3. **What is never collected** — the design §3.3 exclusion list verbatim in spirit: no content, no titles, no paths, no hostnames/usernames/emails, no exact costs, no per-session identifiers, and the server does not log or store IP addresses.
4. **The instance ID** — random UUID derived from nothing, reset button in settings.
5. **How to disable** — the settings toggle (Settings → Global, bottom) and `TWICC_NO_TELEMETRY=1`.
6. **Links** — the TwiCC GitHub repo; the design document path in the repo for full details.

Skeleton (the implementer fills the table rows from design §3 — copy the field lists faithfully, do not invent fields):

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TwiCC telemetry</title>
  <style>
    :root { color-scheme: light dark; }
    body { font: 16px/1.6 system-ui, sans-serif; max-width: 46rem; margin: 0 auto; padding: 2rem 1rem 4rem; }
    h1 { font-size: 1.6rem; } h2 { font-size: 1.15rem; margin-top: 2.2rem; }
    table { border-collapse: collapse; width: 100%; font-size: 0.92rem; }
    th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid color-mix(in srgb, currentColor 18%, transparent); vertical-align: top; }
    code { font-size: 0.88em; }
  </style>
</head>
<body>
  <h1>TwiCC anonymous telemetry</h1>
  <p><!-- section 1 --></p>
  <h2>What is collected</h2>
  <table><!-- section 2: one row per field: name, type/buckets, purpose --></table>
  <h2>What is never collected</h2>
  <ul><!-- section 3 --></ul>
  <h2>The instance identifier</h2>
  <p><!-- section 4 --></p>
  <h2>How to disable it</h2>
  <p><!-- section 5 --></p>
  <p><!-- section 6: links --></p>
</body>
</html>
```

- [ ] **Step 2: Verify locally**

Run: `npm run dev` then open the printed `http://localhost:8787/`.
Expected: the page renders (light and dark). `curl -X POST http://localhost:8787/v1/telemetry -d '{"schema":1,"instance_id":"test-id"}'` returns 204 (local D1 is auto-created; migrations apply with `wrangler d1 migrations apply twicc-telemetry --local` if the insert errors on a missing table). `curl http://localhost:8787/nope` returns 404.

- [ ] **Step 3: Commit**

```bash
git add telemetry-collector/public/index.html
git commit -m "feat(telemetry): public transparency page"
```

---

### Task 4: README — the operational guide

**Files:**
- Create: `telemetry-collector/README.md`

- [ ] **Step 1: Write the README**

This file is the reason the subfolder exists in the repo: every operation must be executable from it without re-research. Required sections, each with exact commands (all run from `telemetry-collector/`):

1. **What this is** — 3 lines: collector for TwiCC anonymous telemetry, Worker + D1, hostname, pointer to the design doc.
2. **Layout** — the file tree with one-line roles.
3. **Prerequisites** — Node ≥ 18, `npm install` (local `wrangler` via npm scripts; no global install), `npx wrangler login` once per machine.
4. **First deployment (runbook)** — the exact Task 6 sequence, kept in sync: create the D1 database, paste the id, apply migrations remotely, deploy, verify. Note that the `database_id` in `wrangler.toml` is **intentionally committed** (D1 ids are not secrets) so a future contributor doesn't "sanitize" it away.
5. **Everyday operations** —
   - update worker/page: edit → `npm test` → `npm run deploy` (deploys code + assets + domain config in one go);
   - watch live traffic: `npm run tail`;
   - local dev: `npm run dev` (+ the `--local` migration command).
6. **Querying the data** — `npx wrangler d1 execute twicc-telemetry --remote --command "…"` with 3 ready-made examples: total payloads per day; distinct instances per day; latest payload of one instance pretty-printed (`json(body)`); plus a pointer to the D1 console in the Cloudflare dashboard for interactive SQL, and `npx wrangler d1 export twicc-telemetry --remote --output=backup.sql` for backups.
7. **Schema changes** — `npx wrangler d1 migrations create twicc-telemetry <name>`, edit the SQL, apply `--local` for tests/dev, then `npm run db:migrate:remote`; never edit an applied migration.
8. **Custom domain & rate limiting** — the domain is declared in `wrangler.toml` (`custom_domain = true`, DNS/cert automatic on deploy); the per-IP rate limit is a **zone-level WAF rate-limiting rule** configured once in the Cloudflare dashboard (Security → WAF → Rate limiting rules): match hostname `twicc-telemetry.twidi.com` + path `/v1/telemetry`, e.g. block 10 s when > 30 requests/10 s per IP. Note explicitly: rate limiting happens at the edge and the worker never sees, logs, or stores IPs.
9. **Contract with the TwiCC client** — endpoint, method, schema versioning rule (unknown `schema` → 400; additive changes within schema 1 are fine since the body is stored raw).

- [ ] **Step 2: Commit**

```bash
git add telemetry-collector/README.md
git commit -m "docs(telemetry): operational README for the collector"
```

---

### Task 5: Plan/design cross-check

**Files:**
- Modify (only if drift is found): `docs/plans/2026-07-18-telemetry-design.md`

- [ ] **Step 1: Verify the built collector against design §7**

Checklist: insert-only ✓ / no IP or UA persisted ✓ / size cap + schema validation ✓ / `GET /` transparency page ✓ / custom domain declared ✓ / reading out of band ✓. If anything diverged during implementation, fix the code or (if the divergence was a deliberate improvement) note it in the design doc — never silently.

- [ ] **Step 2: Commit (only if the design doc changed)**

---

### Task 6: First deployment (user-driven runbook)

This task is executed **by the user** (their Cloudflare account); the agent's role is to walk through it with them and fix anything that surfaces. Everything here is also written in the README (Task 4 §4) — if the two drift, the README wins and gets corrected.

- [ ] **Step 1: Authenticate** — `npx wrangler login` (browser OAuth, once per machine).
- [ ] **Step 2: Create the database** — `npx wrangler d1 create twicc-telemetry`; paste the returned `database_id` into `wrangler.toml`, replacing `REPLACE_AFTER_D1_CREATE`. Commit that one-line change (`chore(telemetry): pin the production D1 database id`).
- [ ] **Step 3: Apply migrations remotely** — `npm run db:migrate:remote`.
- [ ] **Step 4: Deploy** — `npm run deploy`. Expected output: uploaded assets, bound D1, custom domain `twicc-telemetry.twidi.com` attached (DNS record + certificate created automatically; certificate issuance can take a few minutes).
- [ ] **Step 5: Configure the WAF rate-limiting rule** — dashboard, as specified in README §8.
- [ ] **Step 6: Smoke test** —

```bash
curl -i https://twicc-telemetry.twidi.com/                     # 200, HTML page
curl -i -X POST https://twicc-telemetry.twidi.com/v1/telemetry \
  -d '{"schema":1,"instance_id":"smoke-test","instance":{},"days":[]}'   # 204
curl -i https://twicc-telemetry.twidi.com/v1/telemetry          # 405
npx wrangler d1 execute twicc-telemetry --remote \
  --command "SELECT id, received_at, instance_id FROM payloads"  # the smoke row
npx wrangler d1 execute twicc-telemetry --remote \
  --command "DELETE FROM payloads WHERE instance_id = 'smoke-test'"
```

- [ ] **Step 7: Done** — the collector is live; the TwiCC-side client plan can start against the real endpoint.
