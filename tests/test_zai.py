import io
import json
import urllib.error

import pytest

from codex_quota.providers.zai import ZaiError, ZaiProvider, parse_quota


def response():
    return {"success": True, "code": 200, "data": {"limits": [
        {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 0, "nextResetTime": 1800000000000},
        {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 1, "nextResetTime": 1790000000000},
        {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 9, "nextResetTime": 1790100000000},
    ]}}


def test_windows_have_correct_usage_reset_units_and_order():
    snap = parse_quota(response(), now=123)
    assert snap.provider == "zai" and snap.fetched_at == 123
    assert [r.primary.remaining_percent for r in snap.limits] == [99, 91, 100]
    assert [r.primary.window_minutes for r in snap.limits] == [300, 10080, 43200]
    assert snap.limits[2].primary.label == "本月"
    assert snap.limits[0].primary.reset_at == 1790000000
    assert len({r.limit_id for r in snap.limits}) == 3


@pytest.mark.parametrize('payload', [None, {}, {"code": 401, "msg": "secret"},
    {"code": 200, "success": False}, {"code": 200, "data": {"limits": []}}])
def test_business_errors_are_not_successful_empty_quotas(payload):
    with pytest.raises(ZaiError) as error:
        parse_quota(payload)
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('value', [None, "bad", float('nan'), -1, 101])
def test_invalid_percent_does_not_look_like_unused_quota(value):
    payload = response()
    payload['data']['limits'][0]['percentage'] = value
    with pytest.raises(ZaiError):
        parse_quota(payload)


def test_fetch_sends_key_only_in_header(monkeypatch):
    monkeypatch.setenv('TEST_ZAI_KEY', 'fake-secret')
    def open_request(request, **kwargs):
        assert request.full_url == 'https://api.z.ai/api/monitor/usage/quota/limit'
        assert request.get_header('Authorization') == 'Bearer fake-secret'
        return io.BytesIO(json.dumps(response()).encode())
    monkeypatch.setattr('urllib.request.urlopen', open_request)
    assert ZaiProvider(api_key='$TEST_ZAI_KEY').fetch().provider == 'zai'


def test_missing_key():
    with pytest.raises(ZaiError):
        ZaiProvider().fetch()


def test_registered_from_config(tmp_path, monkeypatch):
    from codex_quota.providers.config import save_providers_config
    from codex_quota.providers.base import default_providers
    path = str(tmp_path / 'providers.toml')
    save_providers_config({'zai': {'type': 'zai', 'enabled': True, 'api_key': 'fake'}}, path)
    monkeypatch.setenv('CODEX_QUOTA_PROVIDERS', 'zai')
    providers = default_providers(path)
    assert len(providers) == 1 and isinstance(providers[0], ZaiProvider)
