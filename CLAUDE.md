# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sitechat is a multi-tenant FastAPI backend for an embeddable website chat widget (`static/widget.js`). Each tenant (a customer business) gets a bot grounded in its own documents (RAG over Pinecone) and structured facts (synced from a Google Sheet), which captures leads and emails the business when a visitor needs a human. Deployed to Cloud Run via `cloudbuild.yaml`, which runs the test suite first; a failing test stops the deploy.

## Commands

```bash
pip install -r requirements-dev.txt        # Python 3.12 (CI and Dockerfile); the image installs requirements.txt only
pytest                                     # full suite; CI sets ENVIRONMENT=development GCP_PROJECT_ID=test-project
pytest tests/test_orchestrator.py          # one file
pytest tests/test_orchestrator.py::test_name
pytest tests/browser                       # widget in a real browser; needs: pip install playwright && python -m playwright install chromium webkit (skipped otherwise)
uvicorn app.main:app --reload --port 8080  # local server; widget served at /widget/test-page.html
```

Checks CI runs (all must pass; no formatter, style is left alone):

```bash
ruff check .                                # likely bugs, unused code (ruff.toml)
mypy                                        # types in app/ (mypy.ini)
bandit -q -r app scripts --skip B101        # insecure patterns
pytest --cov=app --cov-fail-under=80        # coverage floor (85% when added)
```

CI also runs pip-audit, gitleaks (secrets in the history) and hadolint (Dockerfile).

Admin/ops scripts are run as modules from the repo root (they talk to real Firestore/Pinecone/OpenAI using `.env`):

```bash
python -m scripts.create_index                     # one-time Pinecone index setup
python -m scripts.create_tenant                    # tenant config is hardcoded in the script
python -m scripts.set_facts / sync_facts <tenant>  # facts by hand, or from the tenant's Google Sheet
python -m scripts.ingest <tenant> docs/<tenant>    # chunk + embed a folder of documents
python -m scripts.inspect_chunks <tenant> [doc_id]
python -m scripts.evaluate <tenant>                # retrieval metrics (recall/precision/MRR) per config
python -m scripts.evaluate_answers <tenant>        # LLM-judged faithfulness; slower and costs more (EVAL_JUDGE_MODEL=gpt-4o in .env: stronger judge, ~15x the cost)
python -m scripts.evaluate_attacks <tenant> [category]   # misuse attempts (off-topic, prompt extraction, jailbreak...); run after prompt or tool changes
python -m scripts.dump_conversation <tenant> > chat.txt
python -m scripts.delete_tenant <tenant> [--conversation <id>]   # dry run; add --delete --confirm <tenant> to delete
python -m scripts.rename_tenant <old> <new> [--site-key K]   # dry run; --copy --confirm <new>, then ingest, sync_facts, delete_tenant <old> (steps in the script)
python -m scripts.unanswered <tenant> [--days N]   # what the bot could not answer; read-only, no model calls
python -m scripts.feedback <tenant> [--days N]     # visitors' thumbs up/down; every thumbs-down with question and reply
python -m scripts.leads <tenant> [--days N] [--all]  # every lead to leads-<tenant>.csv, for Excel or Google Sheets
python -m scripts.smoke_test <base_url> <site_key>   # after a deploy: /health and /config, no chat
```

`scripts/test_eval_metrics.py` is a pytest file living outside `tests/`.

## Architecture

Check `notes/CODEMAP.md` to find where things live, and update it whenever files are added, moved or removed.

**Request flow.** `POST /chat` (`app/api/routes.py`) → `get_tenant` dependency (`app/api/dependencies.py`) resolves the tenant from the `X-Site-Key` header (cached for `tenant_cache_seconds`, unknown keys too), checks `is_active`, and checks `Origin` against `tenant.allowed_domains`. Refusals count against the visitor (`rate_limit_rejected_per_visitor`); past that, requests are refused before the lookup, since route limits run after dependencies. CORS is `*` at the middleware level on purpose; per-tenant domain enforcement happens in `get_tenant`. Rate limiting via slowapi (`app/rate_limit.py`): per visitor, and per tenant (`Tenant.rate_limit`, else `rate_limit_per_tenant`); `/config` and `/feedback` per visitor. `/chat` returns a `reply_id`; `POST /feedback` records the visitor's thumbs up/down on that reply (`app/feedback`), which changes nothing the bot does.

**Orchestrator** (`app/agent/orchestrator.py`) is the single place that defines what a reply is made of; the API layer knows nothing about prompts or retrieval. `generate_reply`:
1. Strips email/phone from the message (`app/leads/extraction.py`) into the lead store *before* the model sees it, so contact details aren't re-sent in history.
2. Works out the conversation's programme focus (`app/programmes/focus.py`: which course or retreat the visitor means, by matching names from the tenant's `programmes.yaml`, carried over between messages on `Conversation.focus`; no model call), then runs the conversation read and retrieval, in parallel when the message names its programme. `app/retrieval/triage.py` skips retrieval for short conversational messages ("hi", "thanks").
3. Builds system messages: tenant prompt, running summary, facts sheet, a nudge to ask for email if the lead has unsent items but no contact, retrieved context, then history.
4. Runs a tool loop (max 3 iterations) with tools from `app/agent/tools.py`: `contact_team` (records a `CONTACT_REQUEST`; the email itself is sent after the reply), and `record_visitor_details` (extraction only; the server stores what the model reports). There is no tool for questions: `persist_turn` records every visitor question with the reply on `Conversation.questions` (`app/agent/questions.py` decides what counts; no model call), and the lead email lists all of them.

`after_reply` runs as a FastAPI background task after the response is sent: `persist_turn` (storing the turn and summarizing once history exceeds `summarize_trigger`), then `notify_team_if_due`, which emails the business via Resend when the lead has contact details and an unsent `CONTACT_REQUEST`, then `flag_for_digest`. Each step runs even if an earlier one raised (`_isolated` logs it). Tests that check stored state or sending must run that step themselves (see `reply_and_store` in `tests/test_orchestrator.py` and `take_turn` in `tests/test_pending_items.py`).

**Lead digest.** Cloud Scheduler calls `POST /internal/lead-digest` (`app/api/internal.py`, Google-signed token from the `lead-digest` service account only) every 5 minutes; `send_due_digests` emails conversations flagged `digest_due` (contact details given, questions not yet emailed) once quiet for `lead_digest_idle_minutes`, even without a contact request. The same call syncs any tenant's facts more than a day old from its Google Sheet (`sync_stale_facts`, `app/facts/sync.py`; the sheet must be shared with `sitechat-runtime`), holding back a sheet that looks emptied. Each conversation is claimed (a Firestore transaction) before it is emailed, so overlapping sweeps do not send twice. Setup: `notes/lead-digest-setup.md`.

**Leads and pending items** (`app/leads/`). Lead emails go to the team only, with Reply-To set to the visitor and a suggested reply for the team to finish and send themselves; we never send to an address a visitor typed, and links in what a visitor wrote are made unclickable (`app/leads/links.py`). At most `lead_emails_per_day` per tenant (counts in `lead_email_counts`); over that, an email is held for the digest until the UTC day turns. When a contact request first has contact details, `/chat` returns `confirmation` and the widget shows it as a card (`Tenant.reply_time` adds "usually ..."). A lead is per conversation. Lead *fields* (name, country, course interest) ride along with emails but never trigger one. *Pending items* (`CONTACT_REQUEST`; `UNANSWERED` only on older leads) are what trigger emails. Only the items in a successful email are marked sent, and `Conversation.questions_emailed` counts the questions already sent, so a returning visitor's second email contains only what is new. The one model call per email (`_compose_for_team`) tidies each question and labels it answered / partly / not answered, by id; the server keeps every recorded question even if the model drops or garbles one. A failed send marks its items failed (`failed_at`) and is not retried, unless `retry_failed_lead_emails` is on, in which case they stay unsent and the next turn retries. Within `lead_email_cooldown_minutes` of an email, a request with no new questions is folded into it rather than emailed again, without a model call.

**Retrieval** (`app/retrieval/pipeline.py`): embed → Pinecone vector search → optional BM25 keyword search fused with weighted RRF → optional Cohere rerank. With a programme focus, results are blended: the focused programme's best sections first (Pinecone filter on the chunk's `programme` tag, set at ingest from `programmes.yaml`), then the best of everything. Each chunk reaches the model labelled with its full heading path, which names its programme. Keyword search and rerank are **off by default** because measurements on the small demo corpus showed they hurt MRR (rationale in `app/config.py`). Re-run `scripts.evaluate` before turning them on. `app/evaluation_config.py` defines the configurations the eval compares.

**Tenant isolation.** Every store method takes `tenant_id`. In Pinecone, the tenant is the namespace, so isolation comes from the database rather than a query filter. Keep that invariant in any new store or query.

**Stores and dependency injection.** Each domain (`tenants`, `conversations`, `facts`, `documents`, `leads`, `usage`, `feedback`) has an ABC store with an `InMemory*` and a `Firestore*` implementation. `create_app(...)` in `app/main.py` accepts store instances; `lifespan` only builds real cloud clients for stores not already set on `app.state`. Tests pass in-memory stores and patch `app.agent.orchestrator.complete_with_tools` / `app.retrieval.pipeline.embed_query`, so the suite needs no network or credentials.

**LLM access** goes only through `app/agent/llm.py` (OpenAI, `gpt-4o-mini` by default), which converts API failures into `LLMError` with a visitor-safe message; the route turns that into a 503. Timeouts are sized to fit Cloud Run's 60 s: 15 s per attempt and one retry, a 40 s `reply_deadline_seconds` shared by a reply's tool rounds, 5 s for embeddings, and `reply_max_tokens` on replies (see `app/config.py`). Each reply's tokens count against a daily budget per tenant (`app/usage/budget.py`, `llm.tracking_usage`); once spent, `generate_reply` raises `LLMError` before doing anything, and the widget shows that 503's message.

## Gotchas

- What the bot calls the business's offerings, visitors and so on is `Tenant.wording`, defaulting to a yoga school's phrases; keep new prompt or email text that names them going through it.
- All configuration comes from `app/config.py` (pydantic-settings, `.env`). Retrieval stages and candidate count for the eval come from `RetrievalConfig` in `app/evaluation_config.py`; production builds one from settings.
- `requirements.txt` (what the service needs) and `requirements-dev.txt` (adds pytest) are UTF-8. Regenerating from PowerShell, `pip freeze > file` writes UTF-16: use `| Out-File -Encoding utf8`, and keep the test packages in the dev file.
- `/health`, not `/healthz`, because Cloud Run intercepts `/healthz`.
- Sentry gets no personal data: `send_default_pii=False`, and also `include_local_variables=False` and `max_request_body_size="never"`, since the first alone still sends stack-frame locals and request bodies (`sentry_options` in `app/main.py`, `tests/test_sentry_privacy.py`).
- Log ids live in contextvars (`app/request_context.py`). FastAPI runs sync dependencies and background tasks in worker threads with copied contexts, so set tenant/conversation with `bound()` where the work runs, not in a dependency; a new thread pool must submit via `contextvars.copy_context().run`.
- `docs/` (tenant source documents for ingest) and `evaluation/history/` (saved eval runs) are gitignored and exist only locally. A tenant's folder holds its documents (school-wide facts in a separate general document, one sub-topic per section; programme details per programme), and `programmes.yaml` (read by `scripts.ingest`). Eval labels name sections (`doc | Heading > Sub`), so renaming a heading means updating `evaluation/questions.py`; `scripts.evaluate` refuses to run if a label group matches no section.
- Conversation and lead writes in the Firestore stores go through `with_expiry` (`app/retention.py`), which stamps `expire_at` for TTL deletion; a full `transaction.set` without it would make that document permanent (`tests/test_retention.py` checks).
- `.env` and `service-account.json` (used for reading tenant Google Sheets) are local secrets. Production gets its secrets from Secret Manager via `cloudbuild.yaml`, and runs as the `sitechat-runtime` service account (Firestore and those secrets only; `notes/runtime-sa-setup.md`). A new secret must be granted to that account too; `tests/test_cloudbuild.py` checks.
