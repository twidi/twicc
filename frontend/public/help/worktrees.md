---
title: "Working with worktrees"
---

A worktree is a second working copy of the same repository, checked out to a
**different branch**, in its **own folder** — sharing the repo's history. It
lets you (or an agent) work on a feature branch without touching your main
checkout.

In TwiCC, a worktree shows up as **its own** [project](help/projects),
automatically linked to the main repository. Its sessions, cost and activity **roll up into the parent
project's view**, so you see everything together instead of siloed.

### Where worktrees are created

The **worktree directory** setting decides where TwiCC creates new worktrees,
and pre-fills the path when you create one. The global one is a **template**
with placeholders resolved per project — `{git_root}` (its git root),
`{project_name}` (its name, or folder name if unnamed) and `{project_basedir}`
(its folder name) — so `{git_root}/.worktrees` keeps worktrees inside each repo
while `/home/me/worktrees/{project_name}` gathers them all in one place. Each
project can override it with its own absolute path — set the global one in
Settings, and a project's in its edit dialog.

### Creating one

You make a worktree while starting a new session in it. In a **New session**
menu, each project row has a worktree button — a branch with a **+** — on its
right; click it to open the worktree dialog. (The button only shows for
projects that are git repositories.)

In the dialog:

- **New worktree** — TwiCC creates the worktree and its branch at the chosen
  location.
- **Existing worktree** — adopt a worktree already on disk that TwiCC doesn't
  know about yet (the "Existing worktree" tab).

Either way, you can start a draft session in the new worktree right away.

### Cleaning up

A worktree keeps showing in the project selector and the New session menus until
you **archive** it — from its edit dialog, or its **⋮** menu in the project
selector at the top of the sidebar (archiving hides it without deleting
anything). That only tidies it out of TwiCC, though: ending the worktree for
real — merging the branch and removing it on disk — is up to you or your agent;
TwiCC doesn't do it.
