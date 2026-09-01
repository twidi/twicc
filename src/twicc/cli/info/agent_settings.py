"""``twicc info agent-settings`` — list per-provider field choices and constraints."""

from __future__ import annotations


def build(provider: str | None, include_disabled: bool = False) -> dict[str, dict]:
    """Return ``{provider: {field: {values: [...]}}}`` (no JSON emission, no Django setup).

    Field keys use the **config-surface** names — the same the CLI flags and
    presets use: the model is keyed ``model`` (not the wire name
    ``selected_model``) and extended thinking ``thinking`` (not
    ``thinking_enabled``); the other fields share their wire name. This mirrors
    ``info presets`` / ``--model`` / ``--thinking`` so a caller scripting a
    ``create-session`` / ``update-session settings`` call sees one vocabulary.

    For each field, every accepted value carries:

    - ``restricted_to``: ``None`` when the value is universally
      available, or the exhaustive list of model identifiers that
      support it (canonical ``family-version`` form plus bare aliases
      for latest entries).
    - ``description``: a short human-readable explanation when the
      provider declares one (currently for every ``permission_mode``
      value across providers, plus ``fast_mode=true`` where supported); absent
      otherwise.

    The ``model`` block lists every accepted ``--model`` value, each with a
    ``latest`` flag (the current flagship of its family); ``info models`` carries
    the richer per-model metadata (family, version, capabilities, retirement).

    Each field block also carries an ``aliases`` map (``{alias: value}``) when
    the provider declares aliases for it — e.g. ``effort`` →
    ``{"min": "low", "max": "max"}``.

    Caller must have already initialised Django.
    """
    from twicc.cli._drop_request.help_strings import tokens_to_alias
    from twicc.cli._drop_request.presets import PRESET_KEY_MAP
    from twicc.cli.info._common import resolve_providers

    # Config-surface names: presets/CLI call the model ``model`` and thinking
    # ``thinking``, not their wire names. ``info agent-settings`` is a config
    # surface, so emit the same names (the inverse of ``PRESET_KEY_MAP``).
    wire_to_public = {wire: public for public, wire in PRESET_KEY_MAP.items()}

    output: dict[str, dict] = {}
    for prov, helpers in resolve_providers(provider, include_disabled=include_disabled):
        choices = helpers.get_agent_settings_choices()
        constraints = helpers.get_agent_settings_constraints()
        descriptions = helpers.get_agent_settings_descriptions()
        aliases = helpers.get_agent_settings_aliases()

        per_field: dict[str, dict] = {}

        # The model leads. It is not in ``choices`` (its values live in the
        # model registry, with richer metadata in ``info models``); surface the
        # accepted ``selected_model`` values here too so the section is
        # self-contained. ``latest`` flags the current flagship of each family.
        # Unavailable models are dropped here — this is the config surface
        # (what you may pass to ``--model``), and neither a disabled nor a
        # retired model is a valid choice (both resolve away to their
        # fallback). ``info models`` still lists the disabled one, flagged
        # ``enabled: false`` with the reason; the retired one is gone for good.
        per_field["selected_model"] = {
            "values": [
                {"value": m["selected_model"], "latest": m["latest"]}
                for m in helpers.serialize_model_registry()
                if m["enabled"] and not helpers.is_model_retired(m["selected_model"])
            ]
        }

        for field, values in choices.items():
            field_constraints = constraints.get(field, {})
            field_descriptions = descriptions.get(field, {})
            entries: list[dict] = []
            for value in values:
                entry: dict = {"value": value}
                if field == "context_max":
                    entry["context_max_alias"] = tokens_to_alias(value)
                entry["restricted_to"] = field_constraints.get(value)
                description = field_descriptions.get(value)
                if description is not None:
                    entry["description"] = description
                entries.append(entry)
            per_field[field] = {"values": entries}

        # Untrusted projects use a restricted permission-mode set: surface it as a
        # sibling ``permission_mode_if_untrusted`` field so a caller scripting
        # create/update-session knows which modes survive there. A session created
        # or updated in an untrusted project has its permission_mode resolved /
        # clamped against this set (with a note on a downgrade). Its alias
        # aliases (``min``/``safe``/``max``) are attached by the loop below.
        untrusted_modes = getattr(helpers, "UNTRUSTED_PERMISSION_MODES", frozenset())
        if untrusted_modes:
            pm_descriptions = descriptions.get("permission_mode", {})
            per_field["permission_mode_if_untrusted"] = {
                "values": [
                    {
                        "value": value,
                        "restricted_to": None,
                        **({"description": pm_descriptions[value]} if value in pm_descriptions else {}),
                    }
                    # Keep ``permission_mode``'s declared order for readability.
                    for value in choices.get("permission_mode", []) if value in untrusted_modes
                ]
            }

        # Aliases (``max`` → flagship, ``open`` → most permissive, ...),
        # resolved to a concrete value per provider.
        for field, field_aliases in aliases.items():
            per_field.setdefault(field, {})["aliases"] = field_aliases

        # Rename wire field names to their config-surface equivalents so the
        # output matches ``info presets`` and the CLI flags.
        output[prov.value] = {
            wire_to_public.get(field, field): block
            for field, block in per_field.items()
        }

    return output
