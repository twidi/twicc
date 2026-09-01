"""``twicc info models`` — list per-provider supported model versions."""

from __future__ import annotations


def build(provider: str | None, include_disabled: bool = False) -> dict[str, list[dict]]:
    """Return ``{provider: [models]}`` (no JSON emission, no Django setup).

    Each entry exposes the canonical ``identifier`` (``family-version``)
    and an ``alias`` field — the bare family name for the latest of its
    family, ``None`` otherwise. ``extra`` is the fully-expanded
    provider-specific capability dict (every flag, including ``false``
    values). ``full_name`` and pricing are intentionally omitted: the
    former is provider-internal SDK plumbing the agent does not need,
    and the latter has its own concerns.

    Models past their retirement date are omitted entirely. They cannot be
    selected anywhere, they only ever grow in number, and a session still
    carrying one is displayed and resumed on its replacement — so nothing
    downstream needs to know they ever existed. A merely *disabled* model
    stays listed, flagged ``enabled: false`` with its ``disable_reason``:
    that state is temporary and reversible.

    Caller must have already initialised Django.
    """
    from twicc.cli.info._common import resolve_providers

    output: dict[str, list[dict]] = {}
    for prov, helpers in resolve_providers(provider, include_disabled=include_disabled):
        entries: list[dict] = []
        for mv in helpers.MODEL_VERSIONS:
            if helpers.is_model_version_retired(mv):
                continue
            entry = {
                "identifier": f"{mv.model}-{mv.version}",
                "alias": mv.model if mv.latest else None,
                "family": mv.model,
                "version": mv.version,
                "latest": mv.latest,
                "enabled": mv.enabled,
                "retirement_date": mv.retirement_date.isoformat() if mv.retirement_date else None,
                "extra": helpers.serialize_model_extra(mv),
            }
            # Only carry the reason for a disabled model — like ``description``,
            # absent otherwise to keep the common case quiet.
            if not mv.enabled and mv.disable_reason:
                entry["disable_reason"] = mv.disable_reason
            entries.append(entry)
        output[prov.value] = entries

    return output
