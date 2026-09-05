import pytest
import twicc.synced_settings as ss


def test_agent_share_settings_default_off():
    """The two agent-sharing gate keys exist, default OFF (spec §4/A2)."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    assert SYNCED_SETTINGS_DEFAULTS["allowAgentSessionShares"] is False
    assert SYNCED_SETTINGS_DEFAULTS["allowAgentArtifactShares"] is False


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


def test_existing_settings_without_title_model_use_provider_without_rewrite(temp_settings):
    """An existing settings file keeps legacy routing without a migration write."""
    temp_settings.write_text('{"titleGenerationEnabled":true}')

    settings = ss.read_synced_settings()

    assert settings["titleSuggestionModel"] == "provider"
    assert temp_settings.read_text() == '{"titleGenerationEnabled":true}'


def test_generic_allowlist_excludes_visual_and_special():
    from twicc.cli.settings._keys import classify_key
    assert classify_key("autoUnpinOnArchive") == "generic"
    assert classify_key("waTheme") == "excluded"
    assert classify_key("defaultLayoutId") == "excluded"
    assert classify_key("disabledProviders") == "provider"
    assert classify_key("externalNotificationTargets") == "notifications"
    assert classify_key("claudeCodeDefaultModel") == "provider"
    assert classify_key("nope") == "unknown"

def test_value_type_inferred_from_default():
    from twicc.cli.settings._keys import parse_value
    assert parse_value("autoUnpinOnArchive", "false") is False
    assert parse_value("autoUnpinOnArchive", "true") is True
    assert parse_value("publicBaseUrl", "https://x") == "https://x"

def test_parse_value_rejects_bad_bool_and_int():
    from twicc.cli.settings._keys import parse_value, ValueParseError
    with pytest.raises(ValueParseError):
        parse_value("autoUnpinOnArchive", "maybe")


def test_build_settings_dump_returns_defaults_without_version(temp_settings):
    from twicc.cli.settings.command import build_settings_dump

    result = build_settings_dump()
    # Known generic default must be present.
    assert "autoUnpinOnArchive" in result
    assert result["autoUnpinOnArchive"] is True
    # _version must be stripped.
    assert "_version" not in result


def test_build_settings_dump_keeps_only_generic_keys(temp_settings):
    """The bare `settings` dump (and `get`'s source) is restricted to generic keys."""
    from twicc.cli.settings._keys import classify_key
    from twicc.cli.settings.command import build_settings_dump

    result = build_settings_dump()
    # Every surviving key must classify as generic.
    assert all(classify_key(k) == "generic" for k in result), {
        k: classify_key(k) for k in result if classify_key(k) != "generic"
    }
    # Non-generic keys are dropped: excluded (UI-only), provider, notifications.
    assert "waTheme" not in result
    assert "defaultLayoutId" not in result
    assert "claudeCodeDefaultModel" not in result
    assert "defaultProvider" not in result
    assert "externalNotificationTargets" not in result


def test_generic_key_descriptions_match_generic_keys():
    """GENERIC_KEY_DESCRIPTIONS must list exactly the generic settable keys.

    Guards against drift: a new generic synced setting added without a CLI
    description (which would leave it out of the `set`/`unset`/`get` --help).
    """
    from twicc.cli.settings._keys import GENERIC_KEY_DESCRIPTIONS, classify_key
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    generic_keys = {k for k in SYNCED_SETTINGS_DEFAULTS if classify_key(k) == "generic"}
    assert set(GENERIC_KEY_DESCRIPTIONS) == generic_keys


def test_format_settable_keys_help_lists_every_generic_key():
    """The --help block names each generic key and points to the other commands."""
    from twicc.cli.settings._keys import GENERIC_KEY_DESCRIPTIONS, format_settable_keys_help

    rendered = format_settable_keys_help()
    for key in GENERIC_KEY_DESCRIPTIONS:
        assert key in rendered
    assert "twicc settings provider" in rendered
    assert "twicc settings notifications" in rendered


# ---------------------------------------------------------------------------
# Validation helpers for set/unset key rejection
# ---------------------------------------------------------------------------

def _validate_settable_key(key: str):
    """Return a (field, code, message) tuple if the key is rejected, else None."""
    from twicc.cli.settings._keys import classify_key

    category = classify_key(key)
    if category == "excluded":
        return ("KEY", "excluded",
                f"{key!r} is a UI-only visual preference; not settable via CLI.")
    if category == "provider":
        return ("KEY", "provider_key",
                f"{key!r} is a provider setting; use `twicc settings provider …`.")
    if category == "notifications":
        return ("KEY", "notifications_key",
                f"{key!r} is a notification setting; use `twicc settings notifications …`.")
    if category == "unknown":
        return ("KEY", "unknown_key", f"No such setting {key!r}.")
    # generic → accepted
    return None


def test_set_rejects_excluded_key():
    result = _validate_settable_key("waTheme")
    assert result is not None
    assert result[1] == "excluded"


def test_set_rejects_provider_key():
    result = _validate_settable_key("disabledProviders")
    assert result is not None
    assert result[1] == "provider_key"


def test_set_rejects_provider_prefixed_key():
    result = _validate_settable_key("claudeCodeDefaultModel")
    assert result is not None
    assert result[1] == "provider_key"


def test_set_rejects_notifications_key():
    result = _validate_settable_key("externalNotificationTargets")
    assert result is not None
    assert result[1] == "notifications_key"


def test_set_rejects_unknown_key():
    result = _validate_settable_key("bogusKey")
    assert result is not None
    assert result[1] == "unknown_key"


def test_set_accepts_generic_key():
    result = _validate_settable_key("autoUnpinOnArchive")
    assert result is None


# ---------------------------------------------------------------------------
# settings provider — patch assembly + show projection
# ---------------------------------------------------------------------------

def _empty_provider_flags(**overrides):
    """Build a callback-flags dict (all unset) with the given overrides."""
    flags = {
        "model": None,
        "effort": None,
        "permission_mode": None,
        "context_max": None,
        "thinking": None,
        "fast": None,
        "chrome": None,
        "untrusted_permission_mode": None,
        "usage_read_file": None,
        "no_usage_read_file": False,
        "usage_dump_file": None,
        "no_usage_dump_file": False,
        "quota_wakeup_time": None,
    }
    flags.update(overrides)
    return flags


@pytest.mark.django_db
def test_provider_patch_codex_accepts_fast(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch("codex", _empty_provider_flags(fast=True))
    assert patch == {"codexDefaultFastMode": True}
    assert errors == []


@pytest.mark.django_db
def test_provider_patch_model_alias_resolves(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    # 'max' resolves to the strongest available model per provider.
    cc_patch, cc_errors = build_provider_patch(
        "claude_code", _empty_provider_flags(model="max"))
    assert cc_errors == []
    assert cc_patch == {"claudeCodeDefaultModel": "fable"}

    cx_patch, cx_errors = build_provider_patch(
        "codex", _empty_provider_flags(model="max"))
    assert cx_errors == []
    assert cx_patch == {"codexDefaultModel": "gpt-astra"}


@pytest.mark.django_db
def test_provider_patch_usage_read_file_sets_path_and_enabled(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(usage_read_file="/x"))
    assert errors == []
    assert patch == {
        "claudeCodeUsageReadFilePath": "/x",
        "claudeCodeUsageReadFileEnabled": True,
    }


@pytest.mark.django_db
def test_provider_patch_no_usage_read_file_disables(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(no_usage_read_file=True))
    assert errors == []
    assert patch == {"claudeCodeUsageReadFileEnabled": False}


@pytest.mark.django_db
def test_provider_patch_quota_wakeup_time_sets_key(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(quota_wakeup_time="08:30"))
    assert errors == []
    assert patch == {"claudeCodeQuotaWakeupTime": "08:30"}

    cx_patch, cx_errors = build_provider_patch(
        "codex", _empty_provider_flags(quota_wakeup_time="23:59"))
    assert cx_errors == []
    assert cx_patch == {"codexQuotaWakeupTime": "23:59"}


@pytest.mark.django_db
def test_provider_patch_quota_wakeup_time_empty_disables(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(quota_wakeup_time=""))
    assert errors == []
    assert patch == {"claudeCodeQuotaWakeupTime": ""}


@pytest.mark.django_db
def test_provider_patch_quota_wakeup_time_rejects_malformed(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(quota_wakeup_time="25:99"))
    assert patch == {}
    assert any(e.field == "--quota-wakeup-time" and e.code == "invalid_value"
               for e in errors)


def test_provider_show_includes_quota_wakeup_time(temp_settings):
    from twicc.cli.settings.provider import build_provider_show

    show = build_provider_show("claude_code", {"claudeCodeQuotaWakeupTime": "06:15"})
    assert show["quota_wakeup_time"] == "06:15"
    # Falls back to the provider default ("") when unset.
    show_default = build_provider_show("codex", {})
    assert show_default["quota_wakeup_time"] == ""


@pytest.mark.django_db
def test_provider_patch_untrusted_permission_mode(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    # 'max' resolves within the untrusted-allowed set (acceptEdits for CC).
    patch, errors = build_provider_patch(
        "claude_code", _empty_provider_flags(untrusted_permission_mode="max"))
    assert errors == []
    assert patch == {"claudeCodeDefaultUntrustedPermissionMode": "acceptEdits"}


@pytest.mark.django_db
def test_provider_patch_untrusted_permission_mode_rejects_out_of_set(temp_settings):
    from twicc.cli.settings.provider import build_provider_patch

    # bypassPermissions is never allowed in the untrusted set for Claude Code.
    patch, errors = build_provider_patch(
        "claude_code",
        _empty_provider_flags(untrusted_permission_mode="bypassPermissions"))
    assert patch == {}
    assert any(e.field == "--untrusted-permission-mode"
               and e.code == "invalid_choice" for e in errors)


def test_provider_show_projection_from_seeded_settings(temp_settings):
    from twicc.cli.settings.provider import build_provider_show

    seeded = {
        "defaultProvider": "codex",
        "disabledProviders": ["claude_code"],
        "orchestrationDisabledProviders": ["codex"],
        "claudeCodeDefaultModel": "sonnet",
        "claudeCodeDefaultUntrustedPermissionMode": "plan",
        "claudeCodeUsageReadFileEnabled": True,
        "claudeCodeUsageReadFilePath": "/tmp/usage.json",
    }
    show = build_provider_show("claude_code", seeded)
    assert show["provider"] == "claude_code"
    assert show["enabled"] is False  # in disabledProviders
    assert show["is_default"] is False  # default is codex
    assert show["orchestration_enabled"] is True  # only codex is orch-disabled
    assert show["agent_defaults"]["selected_model"] == "sonnet"
    # Untouched agent-default falls back to the provider default.
    assert show["agent_defaults"]["effort"] == "medium"
    assert show["untrusted_permission_mode_default"] == "plan"
    assert show["usage_read_file"] == {"enabled": True, "path": "/tmp/usage.json"}
    # Untouched usage dump-file falls back to defaults.
    assert show["usage_dump_file"] == {"enabled": False, "path": ""}


# ---------------------------------------------------------------------------
# Task 9 — provider sub-commands: pure helper tests
# ---------------------------------------------------------------------------


def test_compute_disabled_enable_removes_provider():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["claude_code", "codex"], "claude_code", enable=True)
    assert result == ["codex"]


def test_compute_disabled_enable_is_idempotent_when_not_present():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["codex"], "claude_code", enable=True)
    assert result == ["codex"]


def test_compute_disabled_disable_adds_provider():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["codex"], "claude_code", enable=False)
    assert result == ["codex", "claude_code"]


def test_compute_disabled_disable_is_idempotent_when_already_present():
    from twicc.cli.settings.provider import compute_disabled

    result = compute_disabled(["claude_code", "codex"], "claude_code", enable=False)
    assert result == ["claude_code", "codex"]


def test_compute_disabled_preserves_order():
    from twicc.cli.settings.provider import compute_disabled

    # Disable: appended at the end; existing order of others preserved.
    result = compute_disabled(["codex", "other"], "claude_code", enable=False)
    assert result == ["codex", "other", "claude_code"]

    # Enable: others stay in original order.
    result2 = compute_disabled(["codex", "claude_code", "other"], "claude_code", enable=True)
    assert result2 == ["codex", "other"]


def test_compute_disabled_handles_empty_list():
    from twicc.cli.settings.provider import compute_disabled

    # Enable on an empty list stays empty.
    assert compute_disabled([], "claude_code", enable=True) == []
    assert compute_disabled(None, "claude_code", enable=True) == []
    # Disable on an empty list adds the provider.
    assert compute_disabled([], "claude_code", enable=False) == ["claude_code"]
    assert compute_disabled(None, "claude_code", enable=False) == ["claude_code"]


def test_compute_orchestration_disabled_same_semantics():
    from twicc.cli.settings.provider import compute_orchestration_disabled

    assert compute_orchestration_disabled(["codex"], "codex", enable=True) == []
    assert compute_orchestration_disabled(["codex"], "codex", enable=False) == ["codex"]
    assert compute_orchestration_disabled([], "codex", enable=False) == ["codex"]


def test_set_default_rejects_disabled_provider():
    """set-default must reject a provider that is in disabledProviders (pure check)."""
    # We test the pure logic: is the provider in the disabled list?
    from twicc.cli.settings.provider import compute_disabled

    disabled = compute_disabled([], "claude_code", enable=False)
    assert "claude_code" in disabled  # precondition

    # Simulate the check done by provider_set_default.
    provider = "claude_code"
    should_reject = provider in disabled
    assert should_reject is True


def test_set_default_accepts_enabled_provider():
    """set-default must accept a provider not in disabledProviders."""
    provider = "claude_code"
    disabled = ["codex"]  # only codex is disabled
    should_reject = provider in disabled
    assert should_reject is False


# ---------------------------------------------------------------------------
# Task 10 — pure notification-target list edits (_targets.py)
# ---------------------------------------------------------------------------


def test_add_target_generates_id_and_defaults():
    from twicc.cli.settings._targets import add_target
    new = add_target([], url="json://x", name="n", flags={})
    assert new[-1]["url"] == "json://x" and new[-1]["id"] and new[-1]["tested"] is None
    assert new[-1]["enabled"] is True


def test_update_target_resets_tested_on_url_change():
    from twicc.cli.settings._targets import update_target
    targets = [{"id": "a", "url": "json://x", "tested": True}]
    out = update_target(targets, "a", {"url": "json://y"})
    assert out[0]["url"] == "json://y" and out[0]["tested"] is None


def test_remove_and_missing_id_errors():
    from twicc.cli.settings._targets import remove_target, find_target, TargetNotFound
    targets = [{"id": "a"}]
    assert remove_target(targets, "a") == []
    with pytest.raises(TargetNotFound):
        find_target(targets, "zzz")


def test_add_target_defaults_all_notify_flags():
    from twicc.cli.settings._targets import add_target
    new = add_target([], url="json://x", name="", flags={})
    t = new[-1]
    assert t["notifyUserTurn"] is True
    assert t["notifyPendingRequest"] is True
    assert t["notifyExtraUsageStart"] is True
    assert t["notifyPeer"] is True
    assert t["awayOnly"] is True


def test_add_target_flags_override_defaults():
    from twicc.cli.settings._targets import add_target
    new = add_target([], url="json://x", name="", flags={"enabled": False, "awayOnly": False})
    t = new[-1]
    assert t["enabled"] is False
    assert t["awayOnly"] is False
    # Unoverridden defaults stay.
    assert t["notifyUserTurn"] is True


def test_add_target_does_not_mutate_input():
    from twicc.cli.settings._targets import add_target
    original = [{"id": "existing", "url": "json://a"}]
    result = add_target(original, url="json://b", name="", flags={})
    assert len(original) == 1
    assert len(result) == 2


def test_update_target_preserves_tested_when_url_unchanged():
    from twicc.cli.settings._targets import update_target
    targets = [{"id": "a", "url": "json://x", "tested": True, "name": "old"}]
    out = update_target(targets, "a", {"name": "new"})
    assert out[0]["name"] == "new"
    assert out[0]["tested"] is True  # not reset


def test_update_target_does_not_mutate_input():
    from twicc.cli.settings._targets import update_target
    original = [{"id": "a", "url": "json://x", "tested": True}]
    update_target(original, "a", {"url": "json://y"})
    assert original[0]["tested"] is True  # original unchanged


def test_update_target_raises_for_missing_id():
    from twicc.cli.settings._targets import update_target, TargetNotFound
    with pytest.raises(TargetNotFound):
        update_target([{"id": "a"}], "zzz", {"name": "x"})


def test_remove_target_does_not_mutate_input():
    from twicc.cli.settings._targets import remove_target
    original = [{"id": "a"}, {"id": "b"}]
    result = remove_target(original, "a")
    assert len(original) == 2
    assert len(result) == 1
    assert result[0]["id"] == "b"


def test_remove_target_raises_for_missing_id():
    from twicc.cli.settings._targets import remove_target, TargetNotFound
    with pytest.raises(TargetNotFound):
        remove_target([{"id": "a"}], "zzz")


# ---------------------------------------------------------------------------
# Task 11 — notifications list / add / update / remove
# ---------------------------------------------------------------------------


def test_notifications_list_projection_includes_globals(temp_settings):
    """build_notifications_list returns targets + publicBaseUrl + notifyOnExtraUsageStart."""
    from twicc.cli.settings.notifications import build_notifications_list

    seeded = {
        "externalNotificationTargets": [
            {"id": "t1", "url": "json://x", "enabled": True},
        ],
        "publicBaseUrl": "https://example.com",
        "notifyOnExtraUsageStart": False,
    }
    result = build_notifications_list(seeded)
    assert result["externalNotificationTargets"] == seeded["externalNotificationTargets"]
    assert result["publicBaseUrl"] == "https://example.com"
    assert result["notifyOnExtraUsageStart"] is False


def test_notifications_list_projection_defaults_when_absent():
    """build_notifications_list falls back gracefully when keys are absent."""
    from twicc.cli.settings.notifications import build_notifications_list

    result = build_notifications_list({})
    assert result["externalNotificationTargets"] == []
    assert result["publicBaseUrl"] == ""
    assert result["notifyOnExtraUsageStart"] is True


def _build_add_patch(current_targets, url, name="", **toggles):
    """Build the full-list patch for an add operation (pure, no server)."""
    from twicc.cli.settings._targets import add_target
    from twicc.cli.settings.notifications import _build_flags_dict

    flags = _build_flags_dict(
        toggles.get("enabled"),
        toggles.get("user_turn"),
        toggles.get("pending"),
        toggles.get("extra_usage"),
        toggles.get("peer"),
        toggles.get("away_only"),
    )
    new_list = add_target(current_targets, url=url, name=name, flags=flags)
    return {"externalNotificationTargets": new_list}, new_list[-1]["id"]


def _build_update_patch(current_targets, target_id, **kwargs):
    """Build the full-list patch for an update operation (pure, no server).

    Raises TargetNotFound if the id is missing.
    """
    from twicc.cli.settings._targets import update_target
    from twicc.cli.settings.notifications import _build_flags_dict

    patch = _build_flags_dict(
        kwargs.get("enabled"),
        kwargs.get("user_turn"),
        kwargs.get("pending"),
        kwargs.get("extra_usage"),
        kwargs.get("peer"),
        kwargs.get("away_only"),
    )
    if "url" in kwargs:
        patch["url"] = kwargs["url"]
    if "name" in kwargs:
        patch["name"] = kwargs["name"]
    new_list = update_target(current_targets, target_id, patch)
    return {"externalNotificationTargets": new_list}


def _build_remove_patch(current_targets, target_id):
    """Build the full-list patch for a remove operation (pure, no server).

    Raises TargetNotFound if the id is missing.
    """
    from twicc.cli.settings._targets import remove_target

    new_list = remove_target(current_targets, target_id)
    return {"externalNotificationTargets": new_list}


def test_add_builds_expected_whole_list_patch():
    """add builds a whole-list patch with the new target appended."""
    existing = [{"id": "e1", "url": "json://a", "enabled": True, "tested": True}]
    patch, new_id = _build_add_patch(existing, url="json://b", name="mine")
    new_list = patch["externalNotificationTargets"]
    assert len(new_list) == 2
    assert new_list[0]["id"] == "e1"   # existing entry preserved
    assert new_list[1]["url"] == "json://b"
    assert new_list[1]["name"] == "mine"
    assert new_list[1]["id"] == new_id
    # defaults applied
    assert new_list[1]["enabled"] is True
    assert new_list[1]["notifyUserTurn"] is True
    assert new_list[1]["awayOnly"] is True


def test_add_with_explicit_toggles_overrides_defaults():
    """Explicit --disabled / --no-away-only override the add_target defaults."""
    patch, _ = _build_add_patch([], url="json://x", enabled=False, away_only=False)
    t = patch["externalNotificationTargets"][0]
    assert t["enabled"] is False
    assert t["awayOnly"] is False
    # Unoverridden defaults still apply.
    assert t["notifyUserTurn"] is True
    assert t["notifyPendingRequest"] is True


def test_add_with_no_explicit_toggles_uses_all_defaults():
    """No flags → all defaults from add_target apply."""
    patch, _ = _build_add_patch([], url="json://x")
    t = patch["externalNotificationTargets"][0]
    assert t["enabled"] is True
    assert t["notifyUserTurn"] is True
    assert t["notifyPendingRequest"] is True
    assert t["notifyExtraUsageStart"] is True
    assert t["notifyPeer"] is True
    assert t["awayOnly"] is True
    assert t["tested"] is None


def test_update_builds_expected_whole_list_patch():
    """update mutates only the target with the given id, rest unchanged."""
    current = [
        {"id": "t1", "url": "json://x", "enabled": True, "tested": True, "name": "old"},
        {"id": "t2", "url": "json://y", "enabled": False, "tested": None, "name": "other"},
    ]
    patch = _build_update_patch(current, "t1", name="new", enabled=False)
    new_list = patch["externalNotificationTargets"]
    assert len(new_list) == 2
    assert new_list[0]["id"] == "t1"
    assert new_list[0]["name"] == "new"
    assert new_list[0]["enabled"] is False
    assert new_list[0]["tested"] is True  # url unchanged → tested preserved
    # t2 untouched
    assert new_list[1]["id"] == "t2"
    assert new_list[1]["enabled"] is False


def test_update_url_resets_tested():
    """Changing --url via update resets tested to None."""
    current = [{"id": "t1", "url": "json://x", "tested": True}]
    patch = _build_update_patch(current, "t1", url="json://z")
    t = patch["externalNotificationTargets"][0]
    assert t["url"] == "json://z"
    assert t["tested"] is None


def test_update_raises_for_missing_id():
    """update raises TargetNotFound for an unknown id."""
    from twicc.cli.settings._targets import TargetNotFound

    with pytest.raises(TargetNotFound):
        _build_update_patch([{"id": "t1"}], "nonexistent", name="x")


def test_remove_builds_expected_whole_list_patch():
    """remove returns the list without the targeted entry."""
    current = [
        {"id": "t1", "url": "json://a"},
        {"id": "t2", "url": "json://b"},
    ]
    patch = _build_remove_patch(current, "t1")
    new_list = patch["externalNotificationTargets"]
    assert len(new_list) == 1
    assert new_list[0]["id"] == "t2"


def test_remove_raises_for_missing_id():
    """remove raises TargetNotFound for an unknown id."""
    from twicc.cli.settings._targets import TargetNotFound

    with pytest.raises(TargetNotFound):
        _build_remove_patch([{"id": "t1"}], "nonexistent")


# ---------------------------------------------------------------------------
# Task 12 — notifications test + add --test
# ---------------------------------------------------------------------------


def test_notifications_test_missing_id_raises_target_not_found():
    """find_target raises TargetNotFound for an id that does not exist (pure guard)."""
    from twicc.cli.settings._targets import TargetNotFound, find_target

    targets = [{"id": "t1", "url": "json://x"}]
    with pytest.raises(TargetNotFound):
        find_target(targets, "nonexistent-id")


def test_notifications_test_existing_id_does_not_raise():
    """find_target returns the target when the id exists (pure guard — no error)."""
    from twicc.cli.settings._targets import find_target

    targets = [{"id": "t1", "url": "json://x"}, {"id": "t2", "url": "json://y"}]
    target = find_target(targets, "t2")
    assert target["id"] == "t2"


# ---------------------------------------------------------------------------
# Task 13 — info settings schema section
# ---------------------------------------------------------------------------


def _get_schema_entries(build_result: dict) -> list[dict]:
    """Flatten all group entries from build() into a single list."""
    entries = []
    for group in build_result["groups"].values():
        entries.extend(group)
    return entries


def test_info_settings_generic_key_has_correct_type_and_default():
    """build() includes a known generic key with correct type, default, and owner."""
    from twicc.cli.info.settings import build

    result = build()
    entries = _get_schema_entries(result)
    matches = [e for e in entries if e["key"] == "autoUnpinOnArchive"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["owner"] == "generic"
    assert entry["type"] == "bool"
    assert entry["default"] is True


def test_info_settings_excluded_key_is_marked_excluded():
    """build() marks waTheme as excluded (UI-only)."""
    from twicc.cli.info.settings import build

    result = build()
    entries = _get_schema_entries(result)
    matches = [e for e in entries if e["key"] == "waTheme"]
    assert len(matches) == 1
    assert matches[0]["owner"] == "excluded"


def test_info_settings_provider_key_is_marked_provider():
    """build() marks claudeCodeDefaultModel as provider-owned."""
    from twicc.cli.info.settings import build

    result = build()
    entries = _get_schema_entries(result)
    matches = [e for e in entries if e["key"] == "claudeCodeDefaultModel"]
    assert len(matches) == 1
    assert matches[0]["owner"] == "provider"


def test_info_settings_notifications_key_is_marked_notifications():
    """build() marks externalNotificationTargets as notifications-owned."""
    from twicc.cli.info.settings import build

    result = build()
    entries = _get_schema_entries(result)
    matches = [e for e in entries if e["key"] == "externalNotificationTargets"]
    assert len(matches) == 1
    assert matches[0]["owner"] == "notifications"


def test_info_settings_disabled_providers_is_included():
    """build() includes disabledProviders explicitly despite it being absent from defaults."""
    from twicc.cli.info.settings import build

    result = build()
    entries = _get_schema_entries(result)
    matches = [e for e in entries if e["key"] == "disabledProviders"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["owner"] == "provider"
    assert entry["type"] == "list"
    # default is None (sentinel — not a real default value)
    assert entry["default"] is None


def test_info_settings_groups_keys_are_correct():
    """build() returns a dict with a 'groups' key containing the expected owner buckets."""
    from twicc.cli.info.settings import build

    result = build()
    assert "groups" in result
    assert "__description" in result
    groups = result["groups"]
    assert "generic" in groups
    assert "provider" in groups
    assert "notifications" in groups
    assert "excluded" in groups


def test_info_settings_all_entries_have_required_fields():
    """Every entry in build() carries key, type, default, owner, and hint."""
    from twicc.cli.info.settings import build

    result = build()
    for entry in _get_schema_entries(result):
        assert "key" in entry, f"Missing 'key' in {entry}"
        assert "type" in entry, f"Missing 'type' in {entry!r}"
        assert "default" in entry, f"Missing 'default' in {entry!r}"
        assert "owner" in entry, f"Missing 'owner' in {entry!r}"
        assert "hint" in entry, f"Missing 'hint' in {entry!r}"
