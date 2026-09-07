"""Shared text-filter grammar for frontend-equivalent backend filtering."""


def match_subsequence(query: str, text: str) -> bool:
    """Return whether every query character occurs in text, in order."""
    lower_query = query.lower()
    lower_text = text.lower()
    query_index = 0
    for character in lower_text:
        if query_index >= len(lower_query):
            break
        if character == lower_query[query_index]:
            query_index += 1
    return query_index == len(lower_query)


def literal_needle(query: str) -> str:
    """Return the query without its optional wrapping quotes."""
    if query and query[0] in ('"', "'"):
        quote = query[0]
        needle = query[1:]
        if needle.endswith(quote):
            needle = needle[:-1]
        return needle
    return query


def match_literal_query(query: str, text: str) -> bool:
    """Match the whole query as one case-insensitive substring."""
    needle = literal_needle(query)
    if not needle:
        return True
    return needle.lower() in text.lower()


def match_all_terms_query(query: str, text: str) -> bool:
    """Require every whitespace-separated term as a case-insensitive substring.

    For fields where the subsequence grammar is meaningless — a long message
    body, where a short query occurs in order almost every time. Terms match
    anywhere and in any order, so ``release plan`` finds a text that says
    "planning the release".

    A leading quote switches the whole query to one exact phrase, as everywhere
    else in the shared grammar. Quoting is all-or-nothing here: a phrase is
    never one term among others.
    """
    if query and query[0] in ('"', "'"):
        return match_literal_query(query, text)
    lower_text = text.lower()
    return all(term.lower() in lower_text for term in query.split())


def match_text_query(query: str, text: str) -> bool:
    """Apply the shared fuzzy or leading-quote literal filter grammar."""
    if query and query[0] in ('"', "'"):
        return match_literal_query(query, text)
    return match_subsequence(query, text)
