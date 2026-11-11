Read only baseline.json as the explicit hardware input. Write your proposal only
to candidate.json. Do not edit hardware.json, baseline.json, baseline.html,
workflow assets or any other file. Your normal native/project permissions,
hooks/plugins and configured tools still apply; these instructions are not a
filesystem sandbox. Do not start agents or helpers recursively. Do not install,
download a model, run inference/benchmarks, start servers or discover endpoints.

Optionally research up to three current alternatives using already permitted
usable web tools. No new tool or MCP server may be installed or enabled. Give
2–3 short factual sentences (at most 1200 characters) based on the baseline.
Keep estimated context provisional; loading/fit are not guaranteed and this
host's speed/prefill are unknown. Do not assert numerical fit or performance for
suggestions. Never replace hardware, frozen reference numbers or display.

Write strict UTF-8 JSON, at most 32768 bytes, with exactly:
{"format":"millrace-demo-candidate/v1","research_status":"completed|unavailable|partial|skipped","summary":"Two or three sentences.","suggestions":[],"published_measurements":[]}
Each suggestion has exactly model_id (<=160 characters), artifact_url (HTTPS),
source_urls (1–4 HTTPS primary sources), rationale (<=400 characters). At most 3
suggestions. Without usable web tools use unavailable and empty suggestions and
measurements. When web is disabled use skipped with both lists empty.

Published measurements are optional (at most 3) and are never this host's measured
speed. Each requires exactly source_url (HTTPS primary source), hardware, model,
artifact, quantization, backend, backend_version, context (positive integer),
batch (positive integer), cache, metric, unit, value (nonnegative finite number),
and conditions. All text fields are <=400 characters. Include exact conditions;
if unknown, omit the measurement. Source links are unverified assertions.
URLs must be HTTPS, without credentials, local/private/loopback IPs or local paths.
No HTML, executable content or replacement baseline fields are permitted.

Return the existing json-v1 terminal envelope after saving candidate.json:
{"outcome":"PROPOSED","summary":"Proposal saved","refs":["candidate.json"]}
Use NO_RESEARCH instead of PROPOSED for unavailable/skipped research, or DECLINED
with empty refs if you decline. These outcomes do not directly control execution.
