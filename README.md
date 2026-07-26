# NERIS Preparation Assistant

An experimental, paid FreshSkyAI tool that organizes a **de-identified** after-call narrative into a concise review aid before an authorized human completes the department's NERIS or RMS workflow.

Live URL: <https://nfirs.freshskyai.com>

The repository and existing subdomain retain their historical `NFIRSAssistant` names. NFIRS is decommissioned, NFIRS draft generation is disabled, and this app is a NERIS preparation aid only.

Access includes three previews, then Civic costs $14.99/month with up to 40 usage units per day and 200 per month. Civic covers CivicOps only and does not unlock non-Civic products. Existing subscribers with an eligible broader entitlement remain supported.

## Why the app changed

The U.S. Fire Administration states that (verified July 26, 2026):

- Calendar-year 2026 incident submission is exclusively in NERIS.
- January 31, 2026 was the final date to edit calendar-year 2025 NFIRS incidents.
- NFIRS became unavailable in February 2026.

Primary sources:

- [USFA NFIRS sunset and NERIS transition](https://www.usfa.fema.gov/nfirs/sunset/)
- [USFA National Emergency Response Information System](https://www.usfa.fema.gov/nfirs/neris/)
- [USFA overview of NERIS](https://www.usfa.fema.gov/nfirs/neris/about-neris/)

## What it does

A firefighter can speak using the browser's Web Speech API or type a short, de-identified operational summary. One privacy-restricted AI request organizes only the supplied facts into:

- a plain-language incident overview and category;
- a timeline;
- observed conditions;
- actions and resources mentioned;
- outcomes;
- information that still needs local verification; and
- review warnings.

The output is a preparation aid. It must be compared with source records and the current authorized NERIS or RMS interface.

Voice recognition is browser-provided and may use the browser vendor's speech service. FreshSkyAI receives the resulting transcript rather than microphone audio; typed input remains available when local policy does not allow browser speech processing.

## What it deliberately does not do

- Generate NFIRS reports, modules, or codes.
- Assign NERIS codes or controlled-vocabulary values.
- Claim NERIS schema compliance or readiness to file.
- Import, submit, or connect to NERIS, CAD, or RMS.
- Ask users to enter rosters, CAPIDs, exact addresses, names, phone numbers, emails, account/case/incident identifiers, PHI, patient-care details, or operational secrets. Common identifier and patient-care patterns are rejected before a provider call.
- Store narratives or results in an application database.
- Require an account for the three previews. Continued use requires an eligible monthly subscription authenticated with a verified email; no long-term contract or manual FreshSkyAI involvement is required.

Exact identifiers and any authorized patient-care information belong only in the department's authorized system.

## Privacy and reliability controls

- The `us_public` FreshSkyAI privacy profile rejects likely identifiers before a provider call.
- Privacy rejections return HTTP 422 with a direct correction message.
- Model JSON is checked against a narrow schema before it reaches the browser.
- Requests are capped at 32 KB, narratives at 4,000 characters, and provider output at a small validated envelope.
- Provider output and submitted narratives are never written to application logs.
- API and metrics responses use private/no-store and noindex headers.
- Anonymous POSTs have a lightweight process-local rate limit; Cloud Run instance caps and provider budgets remain the hard cost controls.
- The retired `/api/draft` route fails closed with HTTP 410 and links to the official transition notice.

## Stack and operations

- Flask 3.1 + gunicorn on Cloud Run in `us-central1`.
- FreshSkyAI's privacy-restricted shared LLM chain.
- No database or persistent application storage.
- `min-instances=0`, `max-instances=5`, and CPU throttling remain enabled.
- GitHub Actions tests the app before deployment and retains the Cloud Run budget lock.

## Local verification

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

Built by Fresh Sky LLC as a privacy-first civic-service experiment. It is not affiliated with USFA, FEMA, NERIS, or any state fire agency.
