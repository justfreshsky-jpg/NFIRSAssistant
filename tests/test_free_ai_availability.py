"""No provider access or quota use when the free account is unverified."""
import re
import pytest
import app as module


def test_unverified_ai_keeps_deterministic_pages_and_does_not_meter(monkeypatch):
    monkeypatch.delenv('FRESHSKY_FREE_AI_VERIFICATION_FILE', raising=False)
    monkeypatch.delenv('FRESHSKY_AI_COST_POLICY', raising=False)
    calls=[]
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('external network forbidden')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    app=module.app
    app.config.update(TESTING=True, SECRET_KEY='synthetic-local-key')
    client=app.test_client()
    for path in ('/', '/health'):
        assert client.get(path).status_code == 200
    endpoints=['prepare']
    for rule in app.url_map.iter_rules():
        if rule.endpoint not in endpoints or 'POST' not in rule.methods:
            continue
        path=re.sub(r'<(?:[^:>]+:)?([^>]+)>', 'synthetic', str(rule))
        with client.session_transaction() as session:
            before=dict(session)
        response=client.post(path, json={})
        assert response.status_code == 503
        assert response.get_json()['code'] in ('ai_unavailable', 'official_source_review_required')
        with client.session_transaction() as session:
            assert dict(session) == before
    assert calls == []
