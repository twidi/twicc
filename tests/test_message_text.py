"""The plain text of a message, for the timeline and the full-text search (``providers/message_text.py``)."""

from twicc.providers.helpers import get_provider_helpers
from twicc.providers.message_text import clean_message_text, without_artifact_labels

ARTIFACT = '<twicc:inline-artifact id="turn-styles" src="inline-artifacts/turn-styles/index.html" title="Turn styles" />'
COMMAND = "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n<command-args>**finir** le `truc`</command-args>"


def claude(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


def test_a_text_without_a_twicc_tag_is_returned_untouched():
    text = "**Bold**, a `<command-name>` in code and\n\n> a quote <b>html</b>\n"
    assert clean_message_text(text) == text


def test_an_inline_artifact_tag_becomes_its_title_then_its_id_then_nothing_more():
    assert clean_message_text(f"Voici :\n\n{ARTIFACT}\n\nFin.") == "Voici :\n\n[artifact: Turn styles]\n\nFin."
    assert clean_message_text('<twicc:inline-artifact id="calc" src="inline-artifacts/calc/calc.html" />') == "[artifact: calc]"
    assert clean_message_text("<twicc:inline-artifact />") == "[artifact]"


def test_a_tag_over_several_lines_is_one_tag():
    tag = '<twicc:inline-artifact\n  id="prefs"\n  src="inline-artifacts/prefs/index.html"\n  title="Preferences"\n  height="360"\n/>'
    assert clean_message_text(f"a\n{tag}\nb") == "a\n[artifact: Preferences]\nb"


def test_a_publication_tag_not_alone_on_its_line_is_prose_and_stays():
    text = f"Utilise {ARTIFACT} pour publier."
    assert clean_message_text(text) == text


def test_an_insert_screenshot_tag_becomes_an_image_label_with_its_title():
    assert clean_message_text('Voilà <twicc:insert-screenshot offset="1" title="La page" /> ok') == "Voilà [image: La page] ok"
    assert clean_message_text("<twicc:insert-screenshot />") == "[image]"
    assert clean_message_text('<twicc:insert-screenshot title="a ]b" />') == "[image: a )b]"


def test_a_tag_in_code_is_a_quotation_and_stays():
    fenced = f"Exemple :\n\n```text\n{ARTIFACT}\n```\n"
    assert clean_message_text(fenced) == fenced
    inline = "Écris `<twicc:insert-screenshot />` pour insérer une image."
    assert clean_message_text(inline) == inline
    unclosed = f"```\n{ARTIFACT}\n"
    assert clean_message_text(unclosed) == unclosed
    mixed = f"{ARTIFACT}\n\nEt `<twicc:insert-screenshot />` cité, puis <twicc:insert-screenshot title=\"vrai\" />."
    assert clean_message_text(mixed) == "[artifact: Turn styles]\n\nEt `<twicc:insert-screenshot />` cité, puis [image: vrai]."


def test_claude_gives_the_same_clean_words_to_the_timeline_and_the_search_for_a_plain_message():
    helpers = get_provider_helpers("claude_code")
    parsed = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"Fait.\n\n{ARTIFACT}"}]}}
    assert helpers.extract_indexable_text_from_parsed(parsed) == helpers.extract_timeline_text_from_parsed(parsed) == "Fait.\n\n[artifact: Turn styles]"


def test_codex_gives_the_same_clean_words_to_the_timeline_and_the_search():
    helpers = get_provider_helpers("codex")
    record = {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "AgentMessage", "id": "a", "content": [{"type": "Text", "text": f"Fait.\n\n{ARTIFACT}"}]}}}
    assert helpers.extract_indexable_text_from_parsed(record) == helpers.extract_timeline_text_from_parsed(record) == "Fait.\n\n[artifact: Turn styles]"
    user = {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "id": "u", "content": [{"type": "text", "text": 'Vois <twicc:insert-screenshot title="la page" />'}]}}}
    assert helpers.extract_indexable_text_from_parsed(user) == helpers.extract_timeline_text_from_parsed(user) == "Vois [image: la page]"


def test_claude_unwraps_a_slash_command_for_the_timeline_and_the_search_and_keeps_the_markdown_in_the_timeline_only():
    helpers = get_provider_helpers("claude_code")
    assert helpers.extract_indexable_text_from_parsed(claude(COMMAND)) == "/goal finir le truc"
    assert helpers.extract_timeline_text_from_parsed(claude(COMMAND)) == "/goal **finir** le `truc`"
    bare = "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n<command-args></command-args>"
    assert helpers.extract_indexable_text_from_parsed(claude(bare)) == "/compact"
    assert helpers.extract_timeline_text_from_parsed(claude(bare)) == "/compact"
    spaced = "<command-name>/custom</command-name>\n<command-args>  **bold**  </command-args>"
    assert helpers.extract_timeline_text_from_parsed(claude(spaced)) == "/custom **bold**"
    # Blocks of text and a leading newline: the same.
    assert helpers.extract_timeline_text_from_parsed(claude([{"type": "text", "text": "\n" + bare}])) == "/compact"


def test_claude_keeps_a_message_that_only_quotes_a_command_tag():
    helpers = get_provider_helpers("claude_code")
    text = "La balise `<command-name>` sert à ceci."
    assert helpers.extract_timeline_text_from_parsed(claude(text)) == text
    assert helpers.extract_indexable_text_from_parsed(claude(text)) == text


def test_without_artifact_labels_drops_only_the_lines_that_are_a_label():
    assert without_artifact_labels("a\n[artifact: Turn styles]\nb\n  [artifact]  \nc") == "a\nb\nc"
    prose = "Voir [artifact: x] ici et [image: y]"
    assert without_artifact_labels(prose) == prose
    assert without_artifact_labels("[artifact: x]") == ""
