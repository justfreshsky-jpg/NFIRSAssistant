"""
NFIRS Assistant — Flask app.

Free voice / text → NFIRS-1 (Basic Module) draft generator for U.S. fire
departments. Aimed primarily at volunteer & combination departments who
spend hours per shift on NFIRS paperwork.

The firefighter speaks (browser-native Web Speech API) or types a short
narrative after a call. The app uses an LLM to extract structured fields
and look up the correct NFIRS-5.0 codes (incident type, property use,
actions taken, cause of ignition). Output is a structured draft the
firefighter reviews + copies into their existing NFIRS-5 entry tool.

Zero PHI. No CAD/RMS integration. No accounts. No upsell. No cookies
beyond a Flask session for ephemeral state.

Built by Fresh Sky LLC as a free civic-volunteer offering. Liability:
output is a DRAFT and the human filer is responsible for final accuracy.
"""
import collections
import functools
import json
import logging
import os
import re
import threading

from flask import Response, Flask, jsonify, render_template, request

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32))
app.config.update(
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'true').lower() == 'true',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('nfirs')


_metrics = {'requests_total': 0, 'provider_success': collections.Counter(), 'provider_failure': collections.Counter()}
_metrics_lock = threading.Lock()


def _route_handler(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception:
            logger.exception('Unhandled exception in %s', f.__name__)
            return jsonify(error='An error occurred. Please try again.'), 500
    return wrapper


# Minimal security headers — same set as freshsky_common.security but inlined
# so this app stays standalone (no consumer-portfolio dependency).
@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Permissions-Policy', 'microphone=(self), geolocation=()')
    resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return resp


# Provider calls are centralized in the privacy-restricted shared chain.

from freshsky_common.llm import LLMChain, install_provider_metrics  # noqa: E402

_SHARED_LLM = LLMChain(privacy_profile="us_public")
install_provider_metrics(app)


def _llm_via_shared_chain(system, user):
    return _SHARED_LLM.complete(system=system, user=user) or None


_PROVIDERS = [('shared', _llm_via_shared_chain)]


def _llm(system: str, user: str) -> str:
    last_err = None
    for name, fn in _PROVIDERS:
        try:
            out = fn(system, user)
            if out:
                with _metrics_lock:
                    _metrics['provider_success'][name] += 1
                return out.strip()
        except Exception as e:
            last_err = e
            with _metrics_lock:
                _metrics['provider_failure'][name] += 1
            logger.warning('Provider %s failed: %s', name, e)
    raise RuntimeError(f'All LLM providers failed: {last_err}')


# NFIRS-5.0 reference codes (most common, public domain via USFA).
# This subset covers ~95% of typical volunteer-fire-dept call distribution.
# The LLM uses general knowledge for less common codes; flagging unknowns
# in `fields_needing_review` is preferred over guessing wrong.
NFIRS_INCIDENT_TYPES = {
    '111': 'Building fire', '112': 'Fire in structures other than building',
    '113': 'Cooking fire, confined to container', '114': 'Chimney/flue fire confined to chimney',
    '116': 'Fuel burner/boiler malfunction', '118': 'Trash/rubbish fire contained',
    '131': 'Passenger vehicle fire', '132': 'Road freight or transport vehicle fire',
    '141': 'Forest, woods, or wildland fire', '142': 'Brush or brush-and-grass mixture fire',
    '143': 'Grass fire', '151': 'Outside rubbish fire',
    '311': 'Medical assist - assist EMS crew', '321': 'EMS call, excluding vehicle accident',
    '322': 'Vehicle accident with injuries', '323': 'Motor vehicle/pedestrian accident',
    '324': 'Motor vehicle accident, no injuries',
    '341': 'Search for lost person', '350': 'Extrication, rescue (other)',
    '352': 'Extrication of victim from vehicle', '365': 'Watercraft rescue',
    '381': 'Rescue or EMS standby',
    '411': 'Gasoline or other flammable liquid spill', '412': 'Gas leak (natural or LPG)',
    '413': 'Oil or other combustible liquid spill', '422': 'Chemical spill or leak',
    '424': 'Carbon monoxide incident', '440': 'Electrical wiring/equipment problem (other)',
    '442': 'Overheated motor', '444': 'Power line down', '445': 'Arcing, shorted electrical equipment',
    '451': 'Biological hazard',
    '510': 'Person in distress (other)', '511': 'Lock-out',
    '522': 'Water or steam leak', '531': 'Smoke or odor removal',
    '551': 'Assist police or other governmental agency', '553': 'Public service',
    '554': 'Assist invalid', '561': 'Unauthorized burning',
    '571': 'Cover assignment, standby, moveup',
    '611': 'Dispatched and cancelled en route', '622': 'No incident found on arrival at dispatch address',
    '631': 'Authorized controlled burning', '651': 'Smoke scare, odor of smoke',
    '671': 'HazMat release investigation w/no HazMat',
    '700': 'False alarm or false call (other)', '711': 'Municipal alarm system, malicious false alarm',
    '730': 'System malfunction (other)', '733': 'Smoke detector activation due to malfunction',
    '735': 'Alarm system sounded due to malfunction', '736': 'CO detector activation due to malfunction',
    '743': 'Smoke detector activation, no fire-unintentional',
    '745': 'Alarm system activation, no fire-unintentional',
    '746': 'CO detector activation, no CO',
    '813': 'Wind storm, tornado/hurricane assessment', '814': 'Lightning strike (no fire)',
    '900': 'Special type of incident (other)',
}

NFIRS_PROPERTY_USE = {
    '419': '1 or 2 family dwelling', '429': 'Multifamily dwelling (3+ units)',
    '439': 'Boarding/rooming house', '449': 'Hotel/motel',
    '459': 'Residential board and care', '469': 'Detached residential garage',
    '511': 'Convenience store', '519': 'Food and beverage sales',
    '539': 'Household goods sales', '549': 'Specialty shop',
    '569': 'Bank', '579': 'Motor vehicle/boat sales/services/repair',
    '599': 'Business office',
    '619': 'Day care, in commercial property', '629': 'Laboratory or science laboratory',
    '700': 'Health care, detention, & correction (other)',
    '800': 'Place of worship, funeral parlor',
    '880': 'Vehicle parking area', '891': 'Warehouse',
    '899': 'Industrial/manufacturing', '919': 'Park/playground',
    '926': 'Outbuilding/shed', '931': 'Open land or field',
    '936': 'Vacant lot', '960': 'Street, other type',
    '961': 'Highway/divided highway', '962': 'Residential street/road/driveway',
    '963': 'Bridge/trestle', '000': 'Property use, other',
}

NFIRS_ACTIONS_TAKEN = {
    '11': 'Extinguishment by fire service personnel',
    '12': 'Salvage and overhaul', '13': 'Establish fire lines (wildfire)',
    '14': 'Contain fire (wildland)',
    '21': 'Search', '22': 'Rescue, remove from harm',
    '31': 'Provide first aid and check for injuries',
    '32': 'Provide basic life support (BLS)',
    '33': 'Provide advanced life support (ALS)',
    '34': 'Transport person',
    '41': 'Identify, analyze hazardous materials',
    '42': 'HazMat detection, monitoring, sampling, analysis',
    '43': 'Hazardous materials leak control and containment',
    '51': 'Ventilate', '52': 'Forcible entry',
    '53': 'Evacuate area', '55': 'Establish safe area',
    '57': 'Investigate fire out on arrival',
    '71': 'Assist physically disabled', '72': 'Assist invalid',
    '73': 'Provide manpower', '74': 'Provide equipment',
    '75': 'Provide apparatus', '78': 'Control crowd',
    '81': 'Investigate', '82': 'Investigate fire out on arrival',
    '86': 'Inspect', '91': 'Standby',
    '93': 'Refilling, return to service',
}

NFIRS_CAUSE = {
    '1': 'Intentional', '2': 'Unintentional',
    '3': 'Failure of equipment or heat source',
    '4': 'Act of nature', '5': 'Cause under investigation',
    'U': 'Cause undetermined after investigation',
}


def _format_codes_for_prompt(d: dict) -> str:
    return '\n'.join(f'  {k} = {v}' for k, v in d.items())


_NFIRS_SYSTEM = (
    "You are an NFIRS-5.0 Basic Module (NFIRS-1) report generator for U.S. fire departments. "
    "Given a firefighter's after-call narrative, extract structured fields and assign correct NFIRS codes.\n\n"
    "Output a single JSON object with exactly these fields:\n"
    '{\n'
    '  "incident_date": "YYYY-MM-DD or null",\n'
    '  "alarm_time": "HH:MM 24-hour or null",\n'
    '  "arrival_time": "HH:MM 24-hour or null",\n'
    '  "controlled_time": "HH:MM 24-hour or null",\n'
    '  "last_unit_cleared_time": "HH:MM 24-hour or null",\n'
    '  "incident_address": "street address or null",\n'
    '  "city": "string or null",\n'
    '  "state": "2-letter abbreviation, default NJ if unknown",\n'
    '  "zip": "string or null",\n'
    '  "incident_type_code": "3-digit NFIRS code",\n'
    '  "incident_type_label": "human label",\n'
    '  "aid_given_received": "N | Aid received | Aid given | Mutual aid | Auto aid",\n'
    '  "property_use_code": "3-digit NFIRS code",\n'
    '  "property_use_label": "human label",\n'
    '  "actions_taken": [ {"code": "2-digit NFIRS code", "label": "human label"} ],\n'
    '  "apparatus": [ {"unit": "string", "personnel_count": integer} ],\n'
    '  "personnel_total": integer or null,\n'
    '  "casualties": {"fire_fatalities": int, "fire_injuries": int, "civilian_fatalities": int, "civilian_injuries": int},\n'
    '  "cause_of_ignition_code": "1|2|3|4|5|U or null (only for fire incidents)",\n'
    '  "cause_of_ignition_label": "human label or null",\n'
    '  "estimated_property_loss_usd": integer or null,\n'
    '  "estimated_contents_loss_usd": integer or null,\n'
    '  "narrative_cleaned": "professional 3-6 sentence report-quality narrative",\n'
    '  "fields_needing_review": ["list of field names you had to guess or could not fill"]\n'
    '}\n\n'
    "CRITICAL RULES:\n"
    "- Output ONLY the JSON object. No prose around it.\n"
    "- If the narrative does not provide a field, set it to null and add the field name to fields_needing_review. Do NOT invent values.\n"
    "- For codes: pick the BEST match from the reference tables below. If no good match, use the closest '(other)' code (e.g. 900) and add to fields_needing_review.\n"
    "- actions_taken: list 1-3 actions ONLY those explicitly mentioned or strongly implied by the narrative. Do not pad to 3 if only 1 or 2 actions occurred.\n"
    "- narrative_cleaned: rewrite the firefighter's voice/text input as a clear professional report narrative. Fix grammar. Remove repetition. Keep all factual content. ~3-6 sentences.\n"
    "- All times in 24-hour HH:MM format. '23:47' not '11:47 PM'.\n"
    "- For aid_given_received: 'N' = none, 'Aid received' = your dept got help, 'Aid given' = you helped another dept, 'Mutual aid' = formal mutual-aid agreement, 'Auto aid' = automatic aid pre-arranged.\n\n"
    "NFIRS-5 INCIDENT TYPE CODES (subset, use general knowledge for others):\n"
    + _format_codes_for_prompt(NFIRS_INCIDENT_TYPES)
    + "\n\nNFIRS-5 PROPERTY USE CODES (subset, use general knowledge for others):\n"
    + _format_codes_for_prompt(NFIRS_PROPERTY_USE)
    + "\n\nNFIRS-5 ACTIONS TAKEN CODES (subset):\n"
    + _format_codes_for_prompt(NFIRS_ACTIONS_TAKEN)
    + "\n\nNFIRS-5 CAUSE OF IGNITION CODES (Module-1 only, fire incidents):\n"
    + _format_codes_for_prompt(NFIRS_CAUSE)
)


def _strip_code_fence(s: str) -> str:
    """LLMs sometimes wrap JSON in ```json ... ``` despite the prompt."""
    s = s.strip()
    if s.startswith('```'):
        # Strip ```json ... ``` or ``` ... ```
        s = re.sub(r'^```[a-zA-Z]*\s*', '', s)
        s = re.sub(r'\s*```\s*$', '', s)
    return s.strip()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify(status='ok')


@app.route('/metrics')
def metrics():
    with _metrics_lock:
        return jsonify({
            'requests_total': _metrics['requests_total'],
            'provider_success': dict(_metrics['provider_success']),
            'provider_failure': dict(_metrics['provider_failure']),
        })


@app.route('/api/draft', methods=['POST'])
@_route_handler
def draft():
    data = request.get_json(silent=True) or {}
    text = (data.get('narrative') or '').strip()
    if not text:
        return jsonify(error='Please describe the call.'), 400
    if len(text) > 4000:
        return jsonify(error='Narrative is too long (max 4000 characters).'), 400
    with _metrics_lock:
        _metrics['requests_total'] += 1
    raw = _llm(_NFIRS_SYSTEM, f'FIREFIGHTER NARRATIVE:\n\n{text}')
    raw = _strip_code_fence(raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('LLM returned non-JSON output: %s', raw[:200])
        return jsonify(error='The model returned an unparseable draft. Please try again or rephrase.'), 502
    return jsonify(draft=parsed)


_PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Privacy — NFIRS Assistant</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.6;color:#0f172a}h1{margin-bottom:.5em}h2{margin-top:1.5em;font-size:1.1rem}a{color:#1e3a8a}</style>
</head><body>
<a href="/">← Back to NFIRS Assistant</a>
<h1>Privacy Policy — NFIRS Assistant</h1>
<p><em>Last updated 2026-05-07</em></p>
<h2>What we collect</h2>
<p>NFIRS Assistant is a stateless tool. We do <strong>not</strong> require accounts. We do <strong>not</strong> store the text or voice input you submit. We do <strong>not</strong> upload member rosters, patient data, or any personally identifying information.</p>
<h2>What we send to AI providers</h2>
<p>The text or voice transcript you submit is sent to one of several US/EU-jurisdiction LLM providers (Groq, Cerebras, Mistral, HuggingFace via Together, Sambanova, Cloudflare Workers AI, or Google Gemini) for processing. None of these providers train on inputs from our paid-tier API calls (Gemini's free tier may; we do not pass PII).</p>
<h2>What gets logged</h2>
<p>Standard request metadata (IP address, timestamp, response code) is logged by Google Cloud Run for operational purposes (debugging, abuse prevention) and rotated automatically per Google retention defaults. We do not associate logs with individual users.</p>
<h2>Cookies</h2>
<p>A Flask session cookie is set to remember ephemeral state during your visit. It expires when you close the browser. No third-party tracking, no advertising cookies.</p>
<h2>Children</h2>
<p>Some of our tools (e.g. CAPStudy) are designed to be used by minors aged 12+. We do not collect any personally identifying information from anyone, including minors. Parents/guardians of cadets aged 12-17 may use the tool freely.</p>
<h2>Contact</h2>
<p>Questions: <a href="mailto:admin@freshskyllc.com">admin@freshskyllc.com</a>. Operator: Fresh Sky LLC, Somerset County, NJ.</p>
</body></html>"""

_TERMS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Terms of Use — NFIRS Assistant</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.6;color:#0f172a}h1{margin-bottom:.5em}h2{margin-top:1.5em;font-size:1.1rem}a{color:#1e3a8a}</style>
</head><body>
<a href="/">← Back to NFIRS Assistant</a>
<h1>Terms of Use — NFIRS Assistant</h1>
<p><em>Last updated 2026-05-07</em></p>
<h2>What this is</h2>
<p>NFIRS Assistant is a free volunteer-built tool offered by Fresh Sky LLC for use by U.S. fire departments and EMS. No charge. No contract. No license required.</p>
<h2>What this is not</h2>
<p>NFIRS Assistant is <strong>not</strong> affiliated with any government agency, military service, or official entity. Output is AI-generated and intended as a draft or study aid only — the human user is responsible for verifying accuracy against authoritative current sources before acting on or filing anything.</p>
<h2>Use at your own discretion</h2>
<p>You agree to use the tool in good faith. Do not submit personally identifying information (PII) about third parties, patient health information (PHI), or classified/sensitive operational details. The tool is not designed to handle such data and we do not warrant against any misuse.</p>
<h2>No warranty</h2>
<p>The tool is provided "as is" without warranty of any kind. Fresh Sky LLC disclaims all liability for damages arising from use or misuse of the output.</p>
<h2>Changes</h2>
<p>We may update or discontinue the tool without notice. If a tool is retired, this URL will redirect or be retired in tandem.</p>
<h2>Contact</h2>
<p>Questions: <a href="mailto:admin@freshskyllc.com">admin@freshskyllc.com</a>.</p>
</body></html>"""


@app.route('/robots.txt')
def _robots():
    return Response(
        "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /metrics\nDisallow: /health\n"
        "Sitemap: https://nfirs.freshskyai.com/sitemap.xml\n",
        mimetype='text/plain',
    )


@app.route('/sitemap.xml')
def _sitemap():
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url><loc>https://nfirs.freshskyai.com/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
        '</urlset>\n',
        mimetype='application/xml',
    )


@app.route('/privacy')
def _privacy():
    return Response(_PRIVACY_HTML, mimetype='text/html')


@app.route('/terms')
def _terms():
    return Response(_TERMS_HTML, mimetype='text/html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
