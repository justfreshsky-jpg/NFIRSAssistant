# NFIRS Assistant

Free voice-or-text → NFIRS-1 (Basic Module) draft generator for U.S. fire departments. Aimed at volunteer + combination departments who spend hours per shift on NFIRS paperwork.

Live at: <https://nfirs.freshskyai.com>

## What it does

A firefighter speaks (browser-native Web Speech API) or types a short narrative after a call. The app uses an LLM to extract structured fields and look up the correct NFIRS-5.0 codes (incident type, property use, actions taken, cause of ignition). Output is a structured draft the firefighter reviews + copies into their existing NFIRS-5 entry tool.

## What it deliberately doesn't do

- No CAD/RMS integration (zero-friction install)
- No PHI / patient-care data
- No accounts, no upsell, no contract
- No autonomous filing — output is always a DRAFT for human review

## Stack

- Flask + gunicorn on Cloud Run (us-central1)
- LLM provider chain: Groq → Cerebras → Gemini → Mistral → HuggingFace (auto-fallback, US/EU jurisdictions only)
- No database. No persistence. Stateless.
- `min-instances=0` so it costs $0 when idle

## License

Built by Fresh Sky LLC (Somerset County NJ) as a free volunteer offering for the U.S. fire service.
