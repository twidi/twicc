"""Content-free configuration projection, evaluated only when building a payload."""

from twicc.agent_settings_presets import read_agent_settings_presets
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.providers.state import get_enabled_providers
from twicc.tts.config import SPEECH_FIELDS, openrouter_api_key, read_tts_config, script_config_validated


def build_configuration_block(preferences: dict, layouts: list) -> dict:
    # Mirror SummarySettings.vue/effectiveAdapt, not text-dependent call routing.
    providers = {provider.value for provider in get_enabled_providers()}
    agents = {'haiku': 'claude_code', 'luna': 'codex'}
    choice = preferences.get('summaryModel', 'auto')
    key_present = bool(openrouter_api_key())
    if choice == 'openrouter' and key_present:
        summary = 'openrouter'
    elif choice in agents and agents[choice] in providers:
        summary = choice
    else:
        summary = next((engine for engine, provider in agents.items() if provider in providers), 'none')

    config = read_tts_config()
    entry = next((item for item in config.get('configs', []) if item.get('id') == config.get('active_id')), None)
    tts = {'kind': 'none'}
    if entry:
        if entry.get('kind') == 'script':
            if script_config_validated(entry):
                tts = {'kind': 'script'}
        elif key_present and entry.get('validated') == {field: entry.get(field) for field in SPEECH_FIELDS}:
            # Explicitly approved: the configured OpenRouter model is verbatim,
            # including future model identifiers. No other free configuration escapes.
            tts = {'kind': 'openrouter', 'model': entry.get('model', '')}
    title = preferences.get('titleSuggestionModel', 'provider')
    if title not in ('provider', 'haiku', 'luna', 'openrouter'):
        title = 'provider'
    layout = preferences.get('defaultLayoutId', 'single-pane')
    custom = layout != 'single-pane' and any(item.get('id') == layout for item in layouts)
    # The synthetic defaults preset is not stored in the user catalog.
    from twicc.telemetry.snapshot import bucket, WORKSPACE_BUCKETS
    presets = {provider.value: bucket(len(read_agent_settings_presets(provider).get('presets', [])), WORKSPACE_BUCKETS)
               for provider, _ in get_provider_helpers_registry().items()}
    return {'tts': tts, 'summary_model': summary, 'title_model': title,
            'default_layout': 'custom' if custom else 'single-pane', 'agent_presets_bucket': presets}
