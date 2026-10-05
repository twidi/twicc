---
title: "What are artifacts?"
---

Artifacts are content an agent creates for you, on request. They're kept
outside the project's own files, so your project stays untouched.

The agent builds each one as a file and saves it in the session's artifacts
folder — images, web pages (interactive playgrounds, mini apps, etc.), PDFs,
Markdown, diagrams, audio, video. Ask the agent up front to make it an
artifact and it's more likely to end up as one — being explicit also helps
it grasp what you're after.

You'll find them in that session's **Artifacts** tab, where TwiCC renders
them for you. That tab only appears once the session has at least one
artifact.

From there you can **bookmark** any artifact, giving it a name and a scope:
the project the session belongs to, that project's workspace, or everywhere.

Interactive pages can also **save data**: what you do in the page (choices,
settings, notes, game state…) is stored in a `data/` folder next to the
artifact, and survives closing and reopening it. The agent can read those
files back, so a page can act as a rich form: the agent hands you an
artifact, you make your choices and save, you tell the agent you're done —
it reads them from there. The files appear in the artifacts tree like any
others, so you can always inspect or delete what a page has stored.

For rendered web pages, the round **tools** button in the corner unfolds
extra actions: a **responsive mode** to preview the page at an exact device
size, and an **element selection** mode to click any element on the page
and hand the agent a precise description of it — with an optional note and
screenshot — instead of describing it with words. They work exactly as in
the [Browser tab](help/browser-tab).

The **Artifacts view** (open it with the **Artifacts** button in the icon
bar) gathers the artifacts you've bookmarked. Each artifact appears according
to the scope you chose for it. When creating bookmarks, give them clear, meaningful names so they're
easy to find there.
