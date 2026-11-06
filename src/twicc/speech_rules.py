"""Prompt rules shared by the text adaptation and the summary, so both keep what a speech engine
cannot read aloud out of the spoken text (docs/plans/2026-11-01-tts-speech-rewrite-design.md).

The text holds no braces: both prompts are filled with ``str.format``.
"""

# What the listener never hears. A hash is read letter by letter, a path folder by folder.
SPEECH_NEVER_SAID = (
    "the content of a code block, a command or a diagram (only what it is for); the folders of a path; "
    "the value of a commit hash, an UUID or a token; the address of an URL (only its site)"
)

# The rules for the identifiers a text can hold, one bullet each.
SPEECH_IDENTIFIER_RULES = """\
- A path: its last name only, as written. Example: /home/me/project/logs/server.log → server.log; "in src/app/views/home.vue" → "in home.vue". Never say the folder a file is in, in any language.
- A commit hash, an UUID, a token: drop the value and rewrite the sentence so that it stays true and natural; drop the sentence when it then says nothing. Examples: "fixed in commit 9e04b7a" → "fixed in a commit"; "The commit is 9e04b7a." → nothing.
- An URL: the name of its site, once. Example: https://docs.example.org/guide/setup#step-2 → docs.example.org.
- A name from code: as written, without backticks. Never spell it out, never write "dot", "slash" or "underscore"."""


# The output language. Small models drift to English because the prompt is in English, mostly when
# the text holds English code, file names or quotes. The rule names the drift and the reference: the
# text between the markers, which is the only thing that sets the language. The result holds no
# braces: both prompts are filled with ``str.format``.
def speech_language_rule(tag: str) -> str:
    """The rule for a prompt whose text sits between ``<tag>`` and ``</tag>``."""
    return (
        f"The language of your answer is the language of the text between <{tag}> and </{tag}>: the language "
        "of its sentences, not of the code, file names, commands, product names or quotes it holds. "
        "A text written in French gives an answer in French; a text written in English gives an answer in "
        "English; the same for any other language. This prompt is written in English, and its language "
        "never sets the language of your answer. Never translate."
    )


# The last line before the text: the reminder a model reads just before it starts to write.
def speech_language_reminder(tag: str) -> str:
    return f"Remember: write your answer in the language of the text between <{tag}> and </{tag}>, not in English."
