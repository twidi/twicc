"""Shared immutable records for content storage operations."""

from typing import NamedTuple


UNLOADED = object()


class EncodedContent(NamedTuple):
    payload: bytes
    codec: str
    dictionary_id: int | None
    raw_size: int
    sha256: bytes


class ContentBinding(NamedTuple):
    item_id: int
    session_id: str
    current_id: int
    original_id: int | None


class RootDecision(NamedTuple):
    root_id: str | None
    is_cold: bool
    resolved: bool
    active: bool


class StorageCursor(NamedTuple):
    line_num: int
    rows: int
    bytes: int
    done: bool


class RootPromotionCursor(NamedTuple):
    session_id: str | None
    line_num: int
    fk_slot: int
    rows: int
    bytes: int
    done: bool


class RootStorageSnapshot(NamedTuple):
    root_id: str
    incarnation: str
    epoch: int


class PreparedRepresentation(NamedTuple):
    content_id: int
    observed_codec: str
    observed_dictionary_id: int | None
    encoded: EncodedContent
    policy_version: str
    no_gain: bool
