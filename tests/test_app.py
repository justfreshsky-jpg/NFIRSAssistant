import json
import logging
import re
from pathlib import Path

import pytest
from freshsky_common.privacy import detect_sensitive_data

import app as application


ROOT = Path(__file__).resolve().parents[1]


VALID_PREPARATION = {
    "incident_date": "2026-05-06",
    "incident_category": "Residential cooking fire",
    "incident_overview": (
        "A crew responded to a reported kitchen fire at a two-story residence. "
        "The fire was confined to the kitchen and no injuries were reported."
    ),
    "timeline": [
        {"time": "23:47", "event": "Dispatched for a reported kitchen fire"},
        {"time": "23:53", "event": "Engine 41 arrived with four personnel"},
        {"time": "23:58", "event": "Fire was controlled"},
    ],
    "observed_conditions": ["Light smoke visible from the rear"],
    "actions_taken": ["Advanced an attack line", "Completed a primary search"],
    "resources": ["Engine 41 with four personnel"],
    "outcomes": ["No injuries reported", "Fire confined to the kitchen"],
    "missing_information": ["Verify the final cause in the authorized system"],
    "review_warnings": ["The apparent cause requires local confirmation"],
}


@pytest.fixture
def client():
    application.app.config.update(TESTING=True)
    return application.app.test_client()


def test_home_explains_transition_and_sample_is_deidentified(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "NFIRS is decommissioned" in html
    assert "Calendar-year 2026 incident submission is exclusively in NERIS" in html
    assert "Generate NFIRS-1" not in html
    assert "codes pre-filled" not in html

    sample_match = re.search(r"const SAMPLE = `([^`]*)`;", html)
    assert sample_match
    assert detect_sensitive_data(sample_match.group(1)) == []


def test_civic_access_and_sensitive_data_boundaries_are_explicit(client):
    html = client.get("/").get_data(as_text=True)
    app_source = (ROOT / "app.py").read_text()
    requirements = (ROOT / "requirements.txt").read_text()
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "$14.99/month" in html
    assert "40 usage units/day" in html
    assert "200/month" in html
    assert "does not unlock non-Civic products" in html
    for phrase in (
        "rosters",
        "CAPIDs",
        "PHI",
        "incident or case identifiers",
        "operational secrets",
    ):
        assert phrase in html
    assert "subscription_tier='civic'" in app_source
    assert "workspace_id='civic'" in app_source
    assert "f6d78535e8a473fe64ca4b1e516cbbcad426799b" in requirements
    assert "FRESHSKY_WORKSPACE_ID=civic" in workflow


def test_health_reports_nfirs_generation_disabled(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "product": "neris-preparation",
        "nfirs_generation": "disabled",
    }


def test_retired_nfirs_endpoint_fails_closed_without_provider_call(client, monkeypatch):
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("retired endpoint must not call a provider")

    monkeypatch.setattr(application, "_llm", should_not_run)
    response = client.post("/api/draft", json={"narrative": "test"})
    assert response.status_code == 410
    body = response.get_json()
    assert body["replacement"] == "/api/prepare"
    assert body["official_source"] == application.USFA_NFIRS_SUNSET_URL
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["X-Robots-Tag"].startswith("noindex")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"narrative": ""},
        {"narrative": 42},
        {"narrative": "Alarm investigation.", "unexpected": "ignored?"},
    ],
)
def test_prepare_rejects_invalid_requests(client, payload):
    if payload is None:
        response = client.post("/api/prepare", data="not-json")
    else:
        response = client.post("/api/prepare", json=payload)
    assert response.status_code == 400


def test_prepare_rejects_oversized_narrative_before_provider(client, monkeypatch):
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("invalid input must not call a provider")

    monkeypatch.setattr(application, "_llm", should_not_run)
    response = client.post("/api/prepare", json={"narrative": "x" * 4001})
    assert response.status_code == 400


def test_prepare_rejects_oversized_request_body(client, monkeypatch):
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("oversized input must not call a provider")

    monkeypatch.setattr(application, "_llm", should_not_run)
    response = client.post(
        "/api/prepare",
        data=json.dumps({"narrative": "x", "padding": "y" * (33 * 1024)}),
        content_type="application/json",
    )
    assert response.status_code == 413


def test_prepare_returns_validated_review_aid_and_private_headers(client, monkeypatch):
    monkeypatch.setattr(
        application,
        "_llm",
        lambda *_args, **_kwargs: "```json\n" + json.dumps(VALID_PREPARATION) + "\n```",
    )
    response = client.post(
        "/api/prepare",
        json={"narrative": "Crew responded to a de-identified residential fire."},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["preparation"] == VALID_PREPARATION
    assert "does not assign codes" in body["notice"]
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_exact_street_address_is_rejected_with_422_before_provider(client):
    response = client.post(
        "/api/prepare",
        json={
            "narrative": (
                "Dispatched at 23:47 to 12 Maple Drive for a reported kitchen fire."
            )
        },
    )
    assert response.status_code == 422
    body = response.get_json()
    assert body["code"] == "sensitive_data"
    assert "street_address" in body["detected_categories"]
    assert "authorized NERIS or RMS" in body["error"]


@pytest.mark.parametrize(
    ("narrative", "category"),
    [
        ("CAD number 26-001234: crew investigated an alarm.", "incident_identifier"),
        ("Patient blood pressure was recorded after arrival.", "patient_care"),
    ],
)
def test_operational_identifiers_and_patient_care_are_rejected_before_provider(
    client, monkeypatch, narrative, category
):
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("restricted input must not call a provider")

    monkeypatch.setattr(application, "_llm", should_not_run)
    response = client.post("/api/prepare", json={"narrative": narrative})
    assert response.status_code == 422
    assert category in response.get_json()["detected_categories"]


def test_invalid_model_output_is_not_written_to_logs(client, monkeypatch, caplog):
    raw_provider_output = "PRIVATE_PROVIDER_OUTPUT_MARKER"
    monkeypatch.setattr(application, "_llm", lambda *_args, **_kwargs: raw_provider_output)

    with caplog.at_level(logging.WARNING, logger="neris_preparation"):
        response = client.post(
            "/api/prepare",
            json={"narrative": "De-identified alarm investigation."},
        )

    assert response.status_code == 502
    assert raw_provider_output not in caplog.text
    assert "llm_output_invalid reason=non_json" in caplog.text


def test_oversized_model_output_is_rejected_without_logging_it(
    client, monkeypatch, caplog
):
    raw_provider_output = "PRIVATE_PROVIDER_OUTPUT_MARKER" + ("x" * 20_000)
    monkeypatch.setattr(application, "_llm", lambda *_args, **_kwargs: raw_provider_output)

    with caplog.at_level(logging.WARNING, logger="neris_preparation"):
        response = client.post(
            "/api/prepare",
            json={"narrative": "De-identified alarm investigation."},
        )

    assert response.status_code == 502
    assert raw_provider_output not in caplog.text
    assert "llm_output_invalid reason=too_large" in caplog.text


def test_model_output_with_legacy_code_field_is_rejected(client, monkeypatch):
    invalid = {**VALID_PREPARATION, "incident_type_code": "111"}
    monkeypatch.setattr(application, "_llm", lambda *_args, **_kwargs: json.dumps(invalid))
    response = client.post(
        "/api/prepare",
        json={"narrative": "De-identified residential fire response."},
    )
    assert response.status_code == 502
    assert "could not be validated" in response.get_json()["error"]


def test_model_output_with_filing_claim_is_rejected(client, monkeypatch):
    invalid = {
        **VALID_PREPARATION,
        "incident_overview": "The report is ready to file.",
    }
    monkeypatch.setattr(application, "_llm", lambda *_args, **_kwargs: json.dumps(invalid))
    response = client.post(
        "/api/prepare",
        json={"narrative": "De-identified residential fire response."},
    )
    assert response.status_code == 502


def test_model_output_with_identifier_is_rejected(client, monkeypatch):
    invalid = {
        **VALID_PREPARATION,
        "incident_overview": "The crew responded to 12 Maple Drive.",
    }
    monkeypatch.setattr(application, "_llm", lambda *_args, **_kwargs: json.dumps(invalid))
    response = client.post(
        "/api/prepare",
        json={"narrative": "De-identified residential fire response."},
    )
    assert response.status_code == 502


def test_security_and_policy_headers(client):
    home = client.get("/")
    assert "microphone=(self)" in home.headers["Permissions-Policy"]
    assert "frame-ancestors 'none'" in home.headers["Content-Security-Policy"]
    assert home.headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert "Set-Cookie" not in home.headers

    metrics = client.get("/metrics")
    assert metrics.headers["Cache-Control"].startswith("private, no-store")
    assert metrics.headers["X-Robots-Tag"].startswith("noindex")


def test_privacy_and_terms_match_current_product(client):
    privacy = client.get("/privacy").get_data(as_text=True)
    terms = client.get("/terms").get_data(as_text=True)
    assert "Last updated 2026-07-26" in privacy
    assert "never submitted narratives or model output" in privacy
    assert "does not generate NFIRS reports or codes" in terms
    assert application.USFA_NFIRS_SUNSET_URL in terms
