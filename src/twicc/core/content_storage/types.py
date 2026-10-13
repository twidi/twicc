"""Shared immutable records for content storage operations."""

from typing import NamedTuple


UNLOADED = object()


class EncodedContent(NamedTuple):
    payload: bytes
    codec: str
    dictionary_id: int | None
    raw_size: int
    sha256: bytes


class PreparedInternContent(NamedTuple):
    text: str
    sha256: bytes
    raw_size: int
    existing_id: int | None
    encoded: EncodedContent | None
    policy_version: str | None
    no_gain: bool


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


class SessionPromotionSnapshot(NamedTuple):
    """Operation-local target identity plus the unchanged native-root mutation fence."""
    root_id: str
    incarnation: str
    epoch: int
    session_id: str
    session_incarnation: str
    recovery: bool = False


class PreparedRepresentation(NamedTuple):
    content_id: int
    observed_codec: str
    observed_dictionary_id: int | None
    encoded: EncodedContent
    policy_version: str
    no_gain: bool


class ItemStorageObservation(NamedTuple):
    id: int
    session_id: str
    line_num: int
    content: str
    current_content_id: int | None
    original_content_id: int | None


class ContentRepresentationObservation(NamedTuple):
    id: int
    payload: bytes
    codec: str
    dictionary_id: int | None
    sha256: bytes
    raw_size: int
    session_ids: tuple[str, ...]
    dictionary_provider: str
    compression_attempt_version: str | None
    compression_no_gain: bool


class ContentMetadataObservation(NamedTuple):
    id: int
    codec: str
    dictionary_id: int | None
    sha256: bytes
    raw_size: int
    session_ids: tuple[str, ...]
    dictionary_provider: str
    compression_attempt_version: str | None
    compression_no_gain: bool


class ReconciliationAction(NamedTuple):
    content_id: int
    observed: ContentMetadataObservation
    encoded: EncodedContent | None
    policy_version: str | None
    no_gain: bool


class PreparedComputeBatch(NamedTuple):
    """One bounded phase input; cursor is proposed until publication commits."""
    phase: str
    cursor: object
    rows: tuple
    prepared: tuple
    provider: str | None
    bytes: int
    done: bool
    line_num: int = -1
    observations: tuple = ()
    representations: tuple = ()
    eligibility: tuple = ()


class ComputeStorageProgress(NamedTuple):
    phase: str
    cursor: object
    root_epoch: int | None
    rows: int
    bytes: int
    done: bool


class ComputeStorageObservation(NamedTuple):
    session_id: str
    target: tuple
    ancestry: tuple
    root_id: str | None
    root_incarnation: str | None
    initial_fields: tuple
