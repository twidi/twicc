from datetime import timedelta

import orjson
import pytest

from twicc.telemetry import activity, snapshot, state


@pytest.fixture
def collecting(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'get_data_dir', lambda: tmp_path)
    monkeypatch.setattr(activity, 'is_telemetry_active', lambda: True)
    return tmp_path


def test_activity_is_gated_and_saturates(collecting, monkeypatch):
    token = activity.collection_token()
    for _ in range(110):
        activity.record_activity('manual_summaries', token=token)
    day = state.utc_today().isoformat()
    assert state.ensure_state()['days'][day]['usage']['manual_summaries'] == 101
    monkeypatch.setattr(activity, 'is_telemetry_active', lambda: False)
    activity.record_activity('manual_tts_messages')
    assert 'manual_tts_messages' not in state.ensure_state()['days'][day]['usage']


def test_old_operation_cannot_count_after_reactivation(collecting):
    token = activity.collection_token()
    state.note_active_transition(False)
    state.note_active_transition(True)
    activity.record_activity('manual_summaries', token=token)
    assert not state.ensure_state()['days'].get(state.utc_today().isoformat(), {}).get('usage')


@pytest.mark.parametrize('kind', ['activity', 'client'])
def test_opt_out_between_gate_and_transaction_cannot_record(collecting, monkeypatch, kind):
    active = True
    def disable_after_read():
        nonlocal active
        previous = active
        if active:
            active = False
            state.note_active_transition(False)
        return previous
    monkeypatch.setattr(activity, 'is_telemetry_active', disable_after_read)
    if kind == 'activity':
        activity.record_activity('manual_tts_messages')
    else:
        activity.record_client('profile', {'browser': 'chrome', 'device_type': 'desktop', 'locale': 'en'})
    assert not state.ensure_state()['days']


@pytest.mark.django_db(transaction=True)
def test_full_catchup_retains_all_thirty_complete_days_and_today(collecting):
    today = state.utc_today()
    with state.state_txn() as txn:
        txn.data['last_sent_date'] = (today - timedelta(days=40)).isoformat()
        txn.data['usage_started_date'] = (today - timedelta(days=41)).isoformat()
        txn.data['days'] = {(today - timedelta(days=offset)).isoformat():
            {'usage': {'manual_tts_messages': 1}} for offset in range(31)}
        txn.write()
    state.record_tick(present=True, live_agents=0)
    stored = state.ensure_state()
    assert len(stored['days']) == 31
    payload = snapshot.build_payload(stored)
    assert len(payload['days']) == 30
    assert all(day['manual_tts_messages_bucket'] == '1' for day in payload['days'])


def test_web_reports_require_current_epoch_and_cannot_inject_summary_counts(collecting, monkeypatch):
    epoch = activity.client_epoch()
    activity.record_report({'epoch': epoch, 'metric': 'manual_summaries'})
    activity.record_report({'epoch': 'old', 'metric': 'manual_tts_messages'})
    activity.record_report({'epoch': epoch, 'metric': 'manual_tts_messages'})
    state.record_tick(present=True, live_agents=2)
    entry = state.ensure_state()['days'][state.utc_today().isoformat()]
    assert entry['usage'] == {'manual_tts_messages': 1}
    assert entry['presence_minutes'] == 1
    assert entry['peak_agents'] == 2
    monkeypatch.setattr(activity, 'is_telemetry_active', lambda: False)
    activity.record_report({'epoch': epoch, 'client_id': 'disabled',
        'client': {'browser': 'chrome', 'device_type': 'desktop', 'locale': 'fr'}})
    assert 'clients' not in state.ensure_state()['days'][state.utc_today().isoformat()]


def test_clients_deduplicate_tabs_not_equal_descriptions_and_never_leak_keys(collecting):
    info = {'browser': 'chrome', 'device_type': 'desktop', 'locale': 'fr'}
    activity.record_client('private-profile-one', info)
    activity.record_client('private-profile-one', {**info, 'locale': 'en'})
    activity.record_client('private-profile-two', info)
    stored = state.ensure_state()['days'][state.utc_today().isoformat()]
    assert len(stored['clients']) == 2
    assert 'private-profile' not in orjson.dumps(stored).decode()
    payload = activity.client_payload(stored)
    assert payload['devices_count'] == 2
    assert payload['ui_locales'] == ['en', 'fr']
    assert payload['devices'] == [{'browser': 'chrome', 'device_type': 'desktop', 'count': 2}]
    assert 'client_salt' not in orjson.dumps(payload).decode()


def test_client_validation_and_android_reclassification(collecting):
    activity.record_client('same-profile', {'browser': 'chrome', 'device_type': 'mobile', 'locale': 'fr'})
    activity.record_client('same-profile', {'browser': 'android_app', 'device_type': 'mobile',
                                          'app_version': '0.118.0', 'engine': 'gecko', 'locale': 'fr',
                                          'private': 'not allowed'})
    day = state.ensure_state()['days'][state.utc_today().isoformat()]
    assert activity.client_payload(day)['devices'] == [{
        'browser': 'android_app', 'device_type': 'mobile', 'app_version': '0.118.0', 'engine': 'gecko', 'count': 1,
    }]
    assert 'private' not in orjson.dumps(day).decode()
    activity.record_client('bad-profile', {'browser': 'private arbitrary string', 'device_type': 'desktop', 'locale': 'private'})
    assert len(state.ensure_state()['days'][state.utc_today().isoformat()]['clients']) == 1


@pytest.mark.django_db(transaction=True)
def test_new_metrics_omit_pre_introduction_days(collecting):
    today = state.utc_today()
    st = state.ensure_state()
    st['last_sent_date'] = (today - timedelta(days=4)).isoformat()
    st['usage_started_date'] = (today - timedelta(days=2)).isoformat()
    payload = snapshot.build_payload(st)
    assert 'manual_summaries_bucket' not in payload['days'][0]
    assert 'manual_summaries_bucket' not in payload['days'][1]
    assert payload['days'][2]['manual_summaries_bucket'] == '0'
    assert payload['days'][2]['devices_count'] == 0


@pytest.mark.parametrize('value, expected', [(0, '0'), (1, '1'), (2, '2-5'), (5, '2-5'),
    (6, '6-20'), (20, '6-20'), (21, '21-50'), (50, '21-50'), (51, '51-100'), (100, '51-100'), (101, '101+')])
def test_usage_bucket_edges(value, expected):
    assert snapshot.bucket(value, snapshot.USAGE_BUCKETS) == expected
