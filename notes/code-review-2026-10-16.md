# Code and infrastructure review, 2026-10-16

Whole-codebase review at commit 1be4091 (lead digest), plus the live GCP project. "Confirmed" means checked against production (logs, IAM, HTTP) or traced in the code; "check" means likely, not proven. Backlog numbers in brackets.

## High

**H1. Any visitor can make us email any address [82].** Confirmed in code. `send_lead_email` CCs whatever address the visitor typed (`app/leads/notify.py`). Since the lead digest, no contact request is needed: type someone else's address, ask a question, leave, and 15–20 minutes later that person gets an email from our sender containing text the visitor wrote ("What they asked"). Scripted, that is a spam or phishing relay limited only by the tenant rate limit, and it would get the Resend account suspended or the sender domain blocklisted, which kills lead email for every tenant. Fix (free): stop CCing; send to the team only, with `reply_to` set to the visitor's address, so the team's reply still reaches them. If a visitor copy is wanted later, send it only after the visitor confirms (double opt-in).

**H2. The service runs as the default compute service account, which has Editor [83].** Confirmed (`gcloud run services describe`, project IAM). `675621015256-compute@` holds `roles/editor`, `roles/run.admin` and `roles/artifactregistry.writer`. Any code execution in the app or a dependency would give near-owner control of the project: read every secret, delete Firestore, deploy other images. Fix (free): a dedicated runtime account `sitechat-run@` with only `roles/datastore.user`, `roles/secretmanager.secretAccessor` on the five secrets, and `roles/logging.logWriter`; `--service-account` in `cloudbuild.yaml`. The deploy trigger also uses this account (H3); give Cloud Build its own account with `run.admin`, `artifactregistry.writer` and `iam.serviceAccountUser` on the runtime account only. Once nothing uses it, remove Editor from the compute account. Note the facts sync (65) then needs the sheet shared with `sitechat-run@`.

**H3. Deploys are not gated on the tests [84].** Confirmed (trigger `deploy-on-push`). Every push to `main` builds and deploys, whatever GitHub Actions says; a red test run still ships. Fix (free): a `pytest` step at the start of `cloudbuild.yaml` (python:3.12 image, `pip install -r requirements.txt && pytest`), so a failing test stops the deploy. About 2 extra build minutes, inside the 120 free per day. Later: branch protection and pull requests (27).

**H4. A slow OpenAI turns into a 504 instead of the polite message [85].** Confirmed in code. The client uses a 30 s timeout and the SDK's default 2 retries (up to ~90 s per call), and a turn can make up to 3 model calls, while Cloud Run cuts requests at 60 s. The visitor gets a bare 504, and the thread keeps running. Fix: `max_retries=1`, timeout ~20 s, and a per-turn deadline so `LLMError` (503 and a readable message) fires before Cloud Run's limit. Add `max_tokens` (~500) to reply calls at the same time (see M4).

## Medium

**M1. request_id and tenant_id never reach the logs [86].** Confirmed: no production log entry for a chat has `tenant_id`. `get_tenant` is a sync dependency, so FastAPI runs it in a worker thread with a copy of the context; the values it sets vanish when it returns. The route's `extra={...}` fields are also dropped, because `JsonFormatter` ignores extras. So "everything for this tenant or request" cannot be queried, and logs from two tenants are indistinguishable except by conversation id in the text. Fix: set the context in a small ASGI middleware (request id) and in the route body (tenant id), and have the formatter include `conversation_id`. Add a test that a log record emitted during `/chat` carries both.

**M2. One failure after the reply skips the lead email [87].** Confirmed in code. `after_reply` runs `persist_turn`, then `notify_team_if_due`, then `flag_for_digest`, with no isolation. If the summarisation call (an LLM call, which can raise `LLMError`) or a Firestore write fails, the contact-request email and the digest flag are skipped for that turn. Fix: run each step in its own try/except with `logger.exception`, so one failure does not cancel the others.

**M3. Invalid requests cost Firestore reads with no limit [88].** Confirmed in code. Unknown site keys are not cached, so each request with a made-up key is a Firestore query. `/config` has no rate limit, and on `/chat` the limiter runs after `get_tenant`, so requests rejected for a bad key or domain are never counted. A loop of bad-key requests can burn through the 50,000 free daily reads and start billing. Fix: cache "not found" for a minute, put the per-visitor limit on `/config`, and count rejected requests against the visitor (or rate-limit in a middleware before the tenant lookup).

**M4. The bot can be used as a free general chatbot [29].** Check. The prompt says to answer only from the information given but does not say to decline unrelated tasks, and replies have no `max_tokens`. A visitor asking for an essay or code costs output tokens on our key, up to the tenant limit. Fix: a prompt rule to decline anything unrelated to the business, `max_tokens` on reply calls (H4), and a few attack prompts in the evaluation ("ignore your instructions", "write Python", "what is your system prompt").

**M5. API docs are public in production [89].** Confirmed: `/docs`, `/redoc` and `/openapi.json` return 200 and list `/internal/lead-digest`. Not exploitable by itself (the endpoint checks a Google-signed token), but it publishes the attack surface. Fix: `docs_url=None, redoc_url=None, openapi_url=None` when `is_production`.

**M6. The container may not shut down cleanly [90].** Check. The shell-form `CMD` runs uvicorn under `/bin/sh` (dash in python:3.12-slim), which may not forward Cloud Run's SIGTERM; uvicorn is then killed after 10 s without finishing in-flight requests or background lead emails. Fix: `CMD exec uvicorn ...` (keeps `${PORT}` expansion).

**M7. Test and ML packages ship in the production image [91].** Confirmed (`requirements.txt`). pytest, Pygments, huggingface_hub, tokenizers, hf-xet and fsspec are installed in the image (mostly via cohere, which is off). A larger image and more imports slow the cold start (76) and add packages that could carry vulnerabilities. Fix: `requirements.txt` (runtime) and `requirements-dev.txt` (tests, eval), saved as UTF-8; consider dropping cohere from the image while rerank is off. Also: local `.venv` runs Python 3.14 while CI and production run 3.12, so a 3.14-only feature would pass locally and fail in deploy; recreate the venv on 3.12.

**M8. Overlapping digest sweeps can send one email twice [92].** Check (code path traced; not observed). A manual run during a scheduled one, or a slow sweep overlapping the next, reads the same `digest_due` conversations and both send before either marks them. Fix: claim each conversation in a Firestore transaction (clear `digest_due` and set a `digest_claimed_at`) before composing the email. Also, `due_for_digest` has no ordering, so with more than 40 recently active flagged conversations, idle ones behind them wait; order by `updated_at`.

## Low

- **L1 [93].** `Conversation.questions` grows without limit, while Firestore caps a document at 1 MiB. A scripted visitor could make a conversation unwritable. Cap the stored questions (e.g. 200) and messages.
- **L2 [93].** `record_visitor_details` stores model-supplied strings with no length limit. Cap each field (~200 characters).
- **L3 [32].** No data retention yet. Firestore TTL policies are free: a TTL field on conversations and leads deletes them after, say, 12 months.
- **L4.** Check that Firestore security rules deny all client access (the app uses the server SDK, which bypasses rules; rules only matter if a client SDK or the Firebase console API is ever pointed at the project). Not checked in this review.
- **L5.** `widget.js` is served without `Cache-Control`; customers' pages re-download it often. Set a short max-age (e.g. 1 hour) once the widget is stable.

## Accepted, not findings

- A missing `Origin` is allowed and the site key is public: the domain check stops casual embedding, not scripts. The rate limits are the protection, as documented in `dependencies.py`.
- Rate-limit counters are per instance (24); fine at one instance.
- Contact-request emails run on throttled CPU after the response (77).

## What is already good

- Tenant isolation by Pinecone namespace and a tenant id on every store method; the API layer knows nothing about prompts.
- Contact details are stripped before the model sees them; Sentry has `send_default_pii=False`; secrets come from Secret Manager and are `SecretStr` in code.
- Every visitor- or model-supplied value in the lead email is HTML-escaped, and the widget builds the DOM without `innerHTML`; `privacy_url` is a validated `HttpUrl`, so no `javascript:` links.
- Input validation at the edge (message length, `conversation_id` pattern, rate-limit format), X-Forwarded-For read from the trusted hop, IPv6 grouped by /64.
- The internal endpoint verifies a Google-signed token, audience and the one allowed account, and is off when unset.
- Firestore writes that read-modify-write use transactions; summarisation has a race check; retrieval failures degrade to a facts-only reply.
- Non-root container, pinned base image and pinned dependencies, startup CPU boost on, Sentry and structured JSON logging.
- 259 offline tests with in-memory stores, plus an evaluation harness with a question-set fingerprint.

## Checks to add (all free)

In CI (`.github/workflows/tests.yml`) and the `cloudbuild.yaml` test step [94]:

1. **Tests gate the deploy** (H3).
2. **Ruff** (lint and import order) with a small rule set; fails the build on errors.
3. **Type checking** (mypy or pyright) on `app/`, starting lenient.
4. **pip-audit** for known vulnerabilities in pinned dependencies; **Dependabot** for update pull requests.
5. **Bandit** (Python security lint) and **gitleaks** (secrets accidentally committed).
6. **Coverage** report with a floor (start at the current figure).
7. **Hadolint** on the Dockerfile.
8. **Smoke test after deploy** (58): `scripts.smoke_test` as the last `cloudbuild.yaml` step.

New tests:

- A log record from `/chat` carries `request_id` and `tenant_id` (M1).
- `after_reply` still emails when summarisation raises (M2).
- A bad site key is looked up once per minute, not per request; `/config` is rate-limited (M3).
- Lead email has no CC and sets `reply_to` (H1).
- Two concurrent sweeps send one email (M8, needs the Firestore emulator, 25).
- Attack prompts in the evaluation (M4, 29).
- Production app has no `/docs` (M5).

## Suggested order

H1 and H2 first (an abuse path and the blast radius if anything goes wrong), then H3, H4, M1, M2, M3. All of it is code or IAM, at no cost.
