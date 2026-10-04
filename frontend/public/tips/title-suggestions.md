---
title: "Customize your session titles"
---

TwiCC names your sessions automatically. To control the style, open
**Settings**, then **Title suggestion**. Choose whether each session uses its
provider, Claude Haiku, or GPT-6 Luna.

Edit the prompt to request a language, length limit, prefix, or another style.

Use **{text}** in the prompt as the placeholder for the session content.

**Automatic titles** controls both the first title and later updates.
Title generation must also be enabled. Real sessions get titles from the
backend, even when no session tab is open. Changes appear quietly in the
sidebar and header.

The first check needs one relevant user message. Later checks need at least
**6 new messages and 15 minutes** since the last attempt. A manual stop or
archive triggers a closing check with **3 new messages** since the last
successful check, regardless of time. A normal CLI exit does not trigger it.
Bare slash commands do not count; commands with arguments can count.

TwiCC keeps the current title unless the conversation's main subjects need
better coverage. A failed check retains the messages for a later attempt.
Checks follow session activity; elapsed time alone does not trigger a check.

A live hybrid terminal can receive its first title. Automatic updates wait
until the terminal stops or the session continues outside the terminal.
A stopped hybrid session can receive updates normally.

Ephemeral sessions keep frontend suggestions because they have no saved
session yet. Existing titles from before this feature stay frozen.
Hidden, stale, and subagent sessions do not receive automatic titles.
Eligible sessions created by agents can receive them when no title was supplied.
