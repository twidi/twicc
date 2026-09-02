---
title: "Peer messaging"
---

Peer messaging connects two independent TwiCC instances. It is mainly for two
people who work together from their own machines. You can also pair two of your
own instances, but that is not the main use case.

A peer message never enters a session on the other instance by itself. The
receiving user must read it first. They can refuse it or place it in a session's
composer.

Even then, TwiCC does not send the message to the agent. The user can review or
edit the composer, then decide whether to send it.

### What peers are for

Peers give agents a controlled way to pass work between people. For example,
an agent can send:

- a summary of completed work;
- an API contract or a technical question;
- a screenshot, document, or other useful file;
- a reply to an earlier peer message.

You can also write a message yourself, when the instruction is entirely yours
and an agent would add nothing.

The message must be self-contained. The other instance does not share the
sending session's memory or conversation.

### What peers do not share

Pairing does not give either person access to the other instance. It does not
share projects, sessions, files, terminals, settings, or agent control.

Only the message title, text, attachments, time, and reply relationship cross
between the instances. Local session details stay local.

### Before pairing

Open **Settings → Peers** to set your name and address. Your name identifies
your instance in pairing requests. Your address must be reachable from the
other TwiCC machine.

Leaving the address empty disables peer messaging. Use HTTPS when possible.
Peer calls are machine-to-machine. If your usual TwiCC tunnel asks visitors to
sign in by email, SSO, or another interactive check, do not use that address
for peers. The other TwiCC instance cannot complete that check.

Create a second tunnel address without that gate, and use it as your peer
address. Keep TwiCC's own password enabled.

A peer address that differs from your External address serves peer traffic
only. It serves no page and no live connection, so you do not need a tunnel
path filter. If you enter your External address as the peer address, that
single address serves the app and peer traffic. Every other address, including
`localhost`, never serves peer traffic.

Do not use the dedicated sharing address for peers. TwiCC limits that hostname
to sharing routes, and Settings refuses a peer address on it. See [reaching
TwiCC from anywhere](help/external-url) for the general tunnel and password
setup.

### Pairing two instances

Peer relationships use a request and approval flow. One user sends a pairing
request from **Manage peers**. The other user receives the request and sees a
six-digit verification code.

The receiving user shares that code through a channel they already trust, such
as a call or a private chat. The requester enters it on their own instance.
This confirms that the request reached the intended person.

Verification only unlocks approval. The receiving user still chooses whether
to accept or refuse the relationship. Each user also chooses their own local
name for the peer.

Only users manage peer relationships. Agents cannot create, approve, refuse,
rename, revoke, or reconnect them.

### Sending a message

An agent can list active peers and send a titled message to one of them. A
message can contain text and attachments. No extra sender-side confirmation is
required after the user has approved the peer relationship.

You can also write a message yourself, from **Manage peers**, from the inbox,
or as a reply while you review a message. This form takes a title and a text
only. It carries no attachment and keeps no draft: closing it discards what you
wrote. Ask an agent for anything more: it attaches files and writes the message
for you.

The title should state the topic clearly. The text should include all context
that the receiving person and agent need.

A successful send means the other instance stored the message. It does not
mean that the receiving user delivered it to a session.

### The peer inbox

Open the inbox from **Settings → Peers**, or from the inbox button next to
**Settings**. That button appears as soon as the peer system is set up, even
with an empty inbox. When a request or message needs your attention, TwiCC
also shows a peer inbox badge.

The inbox contains pairing requests, messages awaiting review, and message
history. You can filter messages by peer or by their title and full text.

An incoming-message notification can open the inbox. It cannot deliver the
message or start an agent.

TwiCC can also alert you outside the app when a message or a pairing request
arrives — a sound, a browser notification, or a push to your devices. Turn it
on under **Settings → Notifications → A peer needs you**. No other peer event
sends an alert: they wait for your next visit.

### Reviewing an incoming message

The review shows the sender, title, message, attachments, and any reply
relationship. It also shows when the peer's user wrote the message instead of
their agent. Large text or attachments require an extra load action before
TwiCC renders or downloads them.

You can add an optional note for your agent. TwiCC keeps that note separate
from the peer's message and identifies it as your own text.

You then have four choices:

- place the message in an existing session's composer;
- create a new draft session with the message in its composer;
- mark the message done, when you dealt with it yourself or there is nothing
  to do — no agent receives it;
- refuse the message.

Placing a message in a composer marks it as delivered. It still does not send
the composer or start the agent.

A refusal carries no words. To explain one, answer with **Reply manually** and
refuse in the same step.

Any decision can be changed later, from the message's history entry. The
sender only learns the first one.

### Replies and session suggestions

A peer message can answer an earlier message. The inbox and review show which
message it answers, and the review can open that message. A message that
received replies shows who answered it: the peer, their agent, you, or your
agent.

You can answer a message yourself, with **Reply manually** in the review. TwiCC
proposes the answered title, prefixed with `Re:`. While the answered message
still awaits your decision, the reply form lets you keep it open, mark it done,
or refuse it in the same step. The choice applies only once the reply reached
the peer.

When possible, TwiCC suggests the local session used for the earlier message.
This is only a suggestion. The receiving user can choose another session or a
new draft.

Every reply goes through the same human review. A reply never bypasses the
receiving user's approval.

### Message status

Sent messages use five states:

- **pending** — the receiving user has not decided yet;
- **delivered** — the user placed the message in a session composer. Something
  may follow, from their agent;
- **done** — the user dealt with the message themselves. No agent received it,
  and nothing more will come from one. If they had something to tell you, it
  arrived as a reply;
- **refused** — the user declined the message;
- **failed** — the sender did not receive confirmed acceptance from the other
  instance.

The status records the receiving user's first decision. They can change it
later; the sender is not told.

An agent can check the current status of a message it sent. A failed status can
be uncertain: the other instance might have stored the message before the
connection failed.

### Redelivery and history

A resolved message stays available in the inbox history. The receiving user
can place it in a composer again, mark it done, or refuse it, whatever the
earlier decision. This helps after choosing the wrong session or clearing a
draft.

TwiCC removes attachment bytes seven days after the latest decision. A new
decision restarts that delay, until the bytes are removed. A pending message
keeps its attachments. The text, title, attachment details, and reply
relationship always remain in history. A later redelivery can therefore become
text-only.

### Revocation and reconnection

Each side controls its own peer relationship. Either user can revoke it at any
time. Revocation stops messaging and clears the relationship credentials, but
keeps the local Peer name and complete message history.

Revocation is silent. The other instance learns about it when a later Peer
request is rejected. Its copy of the relationship then appears as **broken**.

A Peer can also become broken when you change or disable your own Peer address.
Set a reachable address, then reconnect each affected Peer manually.

Reconnect uses a new verification code and requires the other user's approval,
like the original pairing. It restores the existing relationship instead of
creating another one, so its local name and message history remain available.

Human review is the main safety boundary. It does not make untrusted content
safe. Read each message before you place it in a composer.
