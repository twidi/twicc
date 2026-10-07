"""Rules of the drop-request wrappers of the services that take attachment refs.

For a drop-request caller (CLI, RPC, MCP), the backend owns the refs once it has the payload
(D14): the wrapper releases them on every outcome, except a send the manager did not deliver
now. Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.5.
"""

from twicc.core.services.attachments.staging import AttachmentError, validate_ref
from twicc.core.services.attachments.types import AttachmentRef

# Codes a manager or the committer raises about the attachments themselves (phase 1 §8).
_ATTACHMENT_CODE_PREFIXES = ("attachment_", "attachments_")


def refs_to_release(payload: dict) -> tuple[AttachmentRef, ...]:
    """The valid refs of ``payload["attachments"]``, unique, in order (best effort).

    Used to release the refs of a payload whatever its outcome, a malformed one included.
    """
    raw = payload.get("attachments")
    if not isinstance(raw, list):
        return ()
    refs: list[AttachmentRef] = []
    for item in raw:
        try:
            ref = validate_ref(item)
        except AttachmentError:
            continue
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


def is_attachment_code(code: str | None) -> bool:
    """True for an error code about the attachments (``attachment_*``, ``attachments_*``)."""
    return isinstance(code, str) and code.startswith(_ATTACHMENT_CODE_PREFIXES)


def message_with_names(exc: BaseException) -> str:
    """The message of *exc*, with the file names it carries (``names``) appended."""
    names = tuple(getattr(exc, "names", ()) or ())
    message = str(exc)
    return f"{message}: {', '.join(names)}" if names else message


LEGACY_FIELDS = ("images", "documents")
LEGACY_FIELDS_MESSAGE = (
    "images and documents are no longer accepted: this twicc CLI is older than the server. "
    "Update the CLI."
)


def has_legacy_fields(payload: dict) -> bool:
    """True when ``images`` or ``documents`` is non-empty (an empty list or no key is accepted).

    An explicit check: ``validate_attachment_frame`` looks at the legacy fields only when
    refs are present.
    """
    return any(payload.get(field) for field in LEGACY_FIELDS)
