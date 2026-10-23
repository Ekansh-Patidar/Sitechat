# Refactor plan

From an architecture review of the code against `notes/architecture-discussion.md` and `notes/CODEMAP.md` (2026-10-14). At review time the suite passed: 80 tests in 3.8s, locally on Python 3.14 (CI uses 3.12), with no network.

## Rules for every step

- **Do not deploy, push, or change anything in Google Cloud** (Cloud Run, Cloud Build, Secret Manager, Firestore data, IAM). Sandeep does that.
- **Ask before running anything that calls paid external services**: `scripts.evaluate`, `scripts.evaluate_answers`, `scripts.ingest`, a local server chatting with real OpenAI, Pinecone, Cohere or Resend keys, and any other script using `.env` keys. `pytest` is fine: it uses no network.
- One step per commit. Run `pytest` before each commit.
- Where a step's verification needs a deploy or a paid call, write down what to run and hand it over instead of running it.

---

## What's solid

- **Tenant isolation is structural.** Conversations and leads use `tenant__conversation` document ids (`conversations/store.py`, `leads/store.py`). Pinecone search is always scoped to `_namespace(tenant_id)` (`vector_store.py:128`). Isolation tests exist for conversations, facts, chunks and BM25.
- **The orchestrator really is the single place that defines a reply.** `routes.py` only handles HTTP concerns.
- **Dependency injection works.** Tests use in-memory stores and patch `complete_with_tools` / `embed_query`, so CI needs no credentials.
- **Contact details never reach the model.** Email and phone are removed before the model call and before storage (`_capture_contact`).
- **Store writes are transactional read-modify-writes** (`append_turn`, `upsert_fields`, `add_item`, `mark_items_sent`), and `replace_history` uses `merge=True`. The "set without merge wipes fields" bug from notes §8 is fixed for current code paths.
- **Model output is rendered as text.** The widget uses DOM calls, not `innerHTML` (`widget.js:364`).
- **Measured retrieval decisions are in config**, with the reasoning written beside the flags.
- **Pending items are a good design.** The server, not the model, decides when an email is due.

---

## What needs rework (most serious first)

### 1. Per-visitor rate limiting probably doesn't work on Cloud Run
`Limiter(key_func=get_remote_address)` keys on `request.client.host`. The Dockerfile runs uvicorn without `--forwarded-allow-ips`, so uvicorn ignores `X-Forwarded-For` from Cloud Run's front end. `client.host` is then likely the proxy's address, so visitors may share one bucket. `rate_limit_per_tenant` is defined but used nowhere, and nothing limits tokens (notes §9, LLM06).

### 2. Summarization can delete a newer turn
`_maybe_summarize` reads the conversation, spends seconds on an LLM call, then `replace_history(..., recent)` overwrites `messages` with the slice it read earlier. A turn written by `append_turn` during that call is lost. Both run as background tasks, so two quick messages are enough.

### 3. The lead email blocks the reply and misses the latest turn
`send_lead_email` runs inside the request: an extra LLM summary call plus Resend. It builds the summary from `store.get(...)`, which does not yet have the current turn (persisted in the background), and may not have the previous one.

### 4. `conversation_id` is unvalidated client input
There is no pattern or length limit, and the value goes straight into Firestore document ids. A `/` produces an odd-segment path, so the request fails with a 500. `__` could collide with a tenant id containing `__` (low risk, since tenant ids are admin-controlled).

### 5. `agent` ↔ `leads` two-way dependency
`orchestrator` imports `leads.notify`; `leads/notify.py` imports `agent.llm.complete` for `_summarise`. There's no import cycle at module level today, but the package boundaries are tangled.

### 6. Retrieval logic duplicated in `evaluation/run.py`
`retrieve_with_config` re-implements `retrieval/pipeline.retrieve`. It always runs RRF, while production skips fusion when keyword search is off. Both read `settings.retrieval_candidates`, now **5**, but the hybrid numbers in notes §5 were measured with 20. Re-running the eval today would quietly change the comparison.

### 7. Unanswered-question detection: the notes and the code disagree
Notes §7 say attempt 3 (server-side regex) was "written but not yet tested". **Confirmed: the regex version was never written.** The current code (commit `38631ea`) is a stronger prompt plus the `record_unanswered` tool, which is attempt 2 again. Recommended direction: extract unanswered questions as structured output at email-composition time (the notes' second option). Correct notes §7 to say the regex was never written.

### 8. Smaller items
- Stale `routes.py` docstring ("returns a stub reply for now…").
- Citations still returned (`routes.py:96`) and rendered (`widget.js:485`), and the prompt asks for `[n]` markers. The notes say to remove them from the UI and keep them in logs; chunk ids are already logged in `pipeline.py`.
- `retrieval_candidates` defined twice in `config.py`, with the first comment describing 20.
- No retention or deletion for conversations and leads.
- Sync endpoints: the default threadpool has 40 threads, Cloud Run's default concurrency is 80. Matters only under load.

---

## What `tests/` covers

| Area | Covered | Missing |
|---|---|---|
| API security | missing/unknown key, wrong/lookalike origin, subdomain, inactive tenant, message length, `/config` exposes only public fields | rate limiting, `LLMError` → 503, missing-Origin behaviour, tenant cache TTL |
| Orchestrator | reply stored, history sent, tenant prompt, tenant isolation, summarization trims | tool-loop cap, unknown tool, bad tool JSON, summarize-vs-append race |
| Leads / pending | extraction + redaction, phone heuristics, email validation, details tool, pending item without email, reminder nudge, unanswered recorded, second email only new items | `notify._format_body`, CC rule, no key / no `team_email` paths |
| Facts | prompt formatting, verbatim, isolation, cache; sheet parsing and diff | — |
| Documents | chunker, ingest ids/titles/rejects/replace/isolation | — |
| Retrieval | RRF, BM25 term/heading/isolation/no-match | `pipeline.retrieve` itself, `triage.needs_retrieval`, `build_context_message` |
| Stores | in-memory tenant store | every Firestore store (emulator tests not built) |
| Eval | metrics, judge parsing | `run.py` |

**A test that cannot fail:** `tests/test_leads.py:122` patches `app.leads.notify.send_lead_email`, but the orchestrator imports the name directly, so the mock is never used and `mock_send.assert_not_called()` always passes. It should patch `app.agent.orchestrator.send_lead_email`.

---

## What to fix first

**Status, 2026-10-15:** steps 1–7 done, plus the lead email moved to a background task (#3: `after_reply` / `notify_team_if_due`; failed sends are not retried unless `retry_failed_lead_emails` is on). Still open:
- **Shared rate-limit counters.** Limits are per instance for now; see the comment in `config.py`.
- **Size each tenant's limit.** Set `rate_limit_per_tenant` from each customer's real traffic before they go live.
- **Structured-output unanswered extraction** (#7).
- **Firestore emulator tests**, which would cover `apply_summary` and `mark_items_sent` in Firestore.
- **Token budgets and retention.**

Ordered by risk and cost; each step is a separate commit.

1. **Fix the vacuous test.** Change the patch target in `tests/test_leads.py` to `app.agent.orchestrator.send_lead_email`. This restores the guard on "never email without contact details".

2. **Rate limiting** (#1).
   - **Client IP.** It's not yet known whether a load balancer sits in front of Cloud Run, so verify before choosing the `X-Forwarded-For` entry:
     - Add a temporary log line in `get_tenant` or the chat route with `request.client.host` and the raw `X-Forwarded-For` header. Hand it over for Sandeep to deploy, then read the result together.
     - Direct `*.run.app` traffic: Google's front end appends the client IP, so use the **last** entry. Behind an external HTTPS load balancer, the client is the **second-to-last** entry. Never use the first entry: the client can set it.
     - Implement the key function in `app/rate_limit.py` with the hop count as a setting in `config.py`, so it's a config change if the topology changes. Remove the temporary log line afterwards.
   - **Per-tenant limit.** Apply `rate_limit_per_tenant` with a second `@limiter.limit` keyed on the site key.
   - **Per-instance counting.** The limiter has no `storage_uri`, so slowapi keeps counters in each process's memory. Each Cloud Run instance counts separately, and `cloudbuild.yaml` allows up to 10 instances (`--max-instances=10`). The real limits are therefore:
     - Up to **10×** the configured value when Cloud Run spreads one visitor's requests across instances: 20/minute becomes as much as 200/minute, and 300/hour per tenant becomes as much as 3000/hour.
     - Counters reset whenever an instance starts. With `--min-instances=0`, that includes every scale-from-zero, so the per-tenant hourly limit is weaker still.
     - Choosing a fix is Sandeep's call, since both options involve Google Cloud or cost. Either use shared storage (Memorystore/Redis via `storage_uri`, a paid GCP resource) or lower the per-instance values so 10× is acceptable. Document whichever is chosen in `config.py`.
   - **Tests.** A 429 test for the visitor limit and the tenant limit, plus a unit test of the key function with sample `X-Forwarded-For` headers.

3. **Validate `conversation_id`** (#4). Add `Field(pattern=r"^[A-Za-z0-9-]{1,64}$")` in `app/api/schemas.py`. Check first that `widget.js` only generates matching ids. Add a 422 test in `test_api_security.py`.

4. **Fix the summarize race** (#2). `replace_history` should drop only the summarized messages and keep anything appended since. In Firestore, do it in a transaction that removes the first `len(overflow)` messages; mirror that in the in-memory store. Add a test that appends between the read and the replace.

5. **Untangle `agent` ↔ `leads`** (#5). `send_lead_email(lead, tenant, summary: str)` takes the summary as text. Move `_summarise` and its prompt into `agent/`, built from the in-memory `messages` plus the current turn; this also partly fixes #3. Update `test_pending_items.py`, and add `_format_body` tests.

6. **Single retrieval path** (#6). Change to `retrieve(..., config: RetrievalConfig | None = None)`, with `candidates` on `RetrievalConfig`. `evaluation/run.py` calls `retrieve`. Add unit tests for `pipeline.retrieve` and `triage`. Re-running `scripts.evaluate` costs money, so ask first, then compare with §5 before changing defaults.

7. **Cleanups.** `routes.py` docstring, the duplicate `retrieval_candidates`, and removing citations from the widget and the `[n]` prompt instruction. Correct notes §7 (the regex was never written), and update `notes/CODEMAP.md` for any moved code.

**Deferred** (bigger, already on the roadmap): Cloud Tasks lead email (#3), structured-output unanswered extraction (#7), Firestore emulator tests, token budgets, retention.

**Why this order:** 1–3 are small changes that close real exposure: a broken guard test, cost and abuse protection, and a crash on bad input. 4 is silent data loss. 5–6 are structural and make the email (#3) and unanswered detection (#7) easier to do properly. Cleanups go last because they carry no risk.

## Verification

- `pytest` (with `ENVIRONMENT=development GCP_PROJECT_ID=test-project`) after each step. The count should only grow from 80.
- Step 1: temporarily make `contact_team` send without contact details, confirm the fixed test fails, then revert.
- Step 2: unit tests cover the key function and the 429s. The production header check needs a deploy, so it's done by Sandeep.
- Step 6: `python -m scripts.evaluate demo-yoga` only after asking, since it calls OpenAI and Pinecone. Compare vector-only with the §5 table (0.97 / 0.96).
- Steps 3 and 5 end to end: a local widget chat that ends in `contact_team` calls OpenAI and Resend, so ask first. Confirm the email summary includes the final turn.
