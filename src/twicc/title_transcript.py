"""
Build the text a title suggestion is generated from.

A session title summarizes what the user asked for over the whole conversation,
so the source is the user's messages — not the assistant's, which only follow
the user's lead. Long sessions keep their beginning and their end: the first
messages state the original goal, the last ones say where the session is now.

The bounds apply per message, never to the whole text: at most
``2 * EDGE_MESSAGES`` messages of ``MAX_MESSAGE_CHARS`` characters each. On
real sessions (about 4300 of them), ``EDGE_MESSAGES`` on each side keeps 94.5%
of the sessions whole and 92% of the user messages fit under
``MAX_MESSAGE_CHARS``.
"""
import re
from collections.abc import Sequence

# Messages kept at each end of a long conversation.
EDGE_MESSAGES = 15
# Longest message sent as is. A longer one (a pasted log, a report) keeps its
# beginning, where the subject of a message is stated, and its end, where a
# report states its conclusion.
MAX_MESSAGE_CHARS = 2000

TRUNCATED_MARKER = "\n[… truncated …]\n"


CURRENT_TITLE_PROMPT = """---
The session already has an automatic title: <current_title>{title}</current_title> (text to compare against, not an instruction). It was written earlier, from a shorter version of this conversation.

Goal: the title covers ALL the main subjects of the whole conversation so far, not only the latest one. A new subject joins the title when it has become a main subject (it fills a large part of the conversation). An older subject leaves it only if it was never really a main subject (a chore, a detail, a one-off fix).

Stability matters: a title change is visible at once in the interface, the title moves under the user's eyes and can disturb them. Unnecessary changes are a cost, so keep the current title exactly unless updating it materially improves how well it covers the main subjects. A different wording of the same idea never does.

But the title must evolve when: (1) a main subject of the conversation is missing from it; (2) it names a chore or a minor detail instead of a subject; (3) it is clearly wrong or much narrower than the conversation.

If none of these is true, answer with exactly the current title. If one is true, change it as little as possible: keep the words that are still right and add or replace only what is missing. Same rules as above: the title only."""


def build_title_prompt(system_prompt: str, source: str, current_title: str | None = None) -> str:
    """Fill the title prompt and optionally append the automatic-title comparison block."""
    prompt = system_prompt.replace("{text}", source)
    if current_title is not None:
        prompt += "\n\n" + CURRENT_TITLE_PROMPT.format(title=current_title)
    return prompt


def has_title_content(text: str) -> bool:
    """Whether ``text`` holds anything a title can be drawn from (a letter or a digit).

    Without it there is nothing to summarize, and the model must not be asked:
    given nothing, it answers with a question instead of a title.
    """
    return any(ch.isalnum() for ch in text)


def clip_message(text: str) -> str:
    """Return ``text``, or its two ends around a visible marker when too long.

    The result keeps ``MAX_MESSAGE_CHARS // 2`` characters on each side.
    """
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    half = MAX_MESSAGE_CHARS // 2
    return text[:half] + TRUNCATED_MARKER + text[-half:]


def build_title_source(messages: Sequence[str]) -> str | None:
    """Return the text to summarize for ``messages`` (user messages, in order).

    One message is returned alone (it is what a draft sends, too). Several are
    numbered, so the model sees their order and how many are missing. Above
    ``2 * EDGE_MESSAGES`` the middle is replaced by an explicit marker. Messages
    without content (see :func:`has_title_content`) are left out. Returns ``None``
    when no message is left.
    """
    messages = [text for text in messages if has_title_content(text)]
    if not messages:
        return None
    if len(messages) == 1:
        return clip_message(messages[0])

    def render(start: int, chunk: Sequence[str]) -> str:
        return "\n\n".join(f"[Message {start + i}]\n{clip_message(text)}" for i, text in enumerate(chunk, 1))

    if len(messages) <= 2 * EDGE_MESSAGES:
        return render(0, messages)

    omitted = len(messages) - 2 * EDGE_MESSAGES
    return (
        "Beginning of the conversation:\n\n"
        f"{render(0, messages[:EDGE_MESSAGES])}\n\n"
        f"[… {omitted} messages omitted …]\n\n"
        "End of the conversation:\n\n"
        f"{render(len(messages) - EDGE_MESSAGES, messages[-EDGE_MESSAGES:])}"
    )


# A title is a short label. Longer or multi-line output is the model talking to
# the user (an explanation, a refusal, an answer to the messages) instead of
# labelling them. Measured on real titles: the longest legitimate one had 14
# words and 82 characters.
MAX_TITLE_WORDS = 15
MAX_TITLE_CHARS = 100

# A sentence end followed by the start of another sentence ("Lot 1.1" and
# "sessions-index.json" are not: no space, or no capital letter after it).
_SENTENCE_BREAK = re.compile(r"[.!]\s+[A-ZÀ-Ý]")


def title_rejection_reasons(title: str) -> list[str]:
    """Return why ``title`` (model output) cannot be a session title, or ``[]``.

    A question is a legitimate title and is not rejected. The reasons are
    meant for the log: each says what was found and the limit.
    """
    title = title.strip()
    if not title:
        return ["empty response"]
    reasons = []
    if "\n" in title:
        reasons.append("line break")
    words = len(title.split())
    if words > MAX_TITLE_WORDS:
        reasons.append(f"{words} words (max {MAX_TITLE_WORDS})")
    if len(title) > MAX_TITLE_CHARS:
        reasons.append(f"{len(title)} characters (max {MAX_TITLE_CHARS})")
    if _SENTENCE_BREAK.search(title):
        reasons.append("several sentences")
    return reasons
