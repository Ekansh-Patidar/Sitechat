# Backlog

Everything outstanding, numbered continuously (1–81). Collected 2026-10-15 from a review in claude.ai, with status checked against the code the same day. Item 23 (load testing) is replaced by the Testing section (45–61).

Status: **done**, **partly** (with what is missing), or open (no tag).

## Bot behaviour

1. **done** — Unanswered-question detection. Replaced: every visitor question is recorded per turn (`Conversation.questions`, no model call) and the lead email lists all of them, labelled answered / partly / not answered.
2. **Dropped (2026-10-18): not wanted.** Tried and reverted (2026-10-16): Streaming replies word by word (`POST /chat/stream`, server-sent events; commit 7221a02, reverted in d2e3b9d). The user found word-by-word text felt robotic. Replaced by reply bubbles (95). Bring back from 7221a02 if wanted, e.g. streaming one bubble at a time.
3. **done** — Remove citations from the widget, keep them in logs only.
4. Enrichment question (country and intended month after the email arrives) fires inconsistently. Suggested fix: a server-side note once, after the email arrives and the fields are empty, like the "email already on file" note.
5. **Not needed for now (2026-10-18)**: the model's wording has been fine; changing it would need wide (paid) testing first. Was partly done: fixed tool-result wording ("predefined messages"). Tool results are fixed server strings; the reply wording is still the model's.
6. **Done (2026-10-18).** ~~Verify the "don't invent list items or counts" rule holds: add list questions to the evaluation.~~ Five `list` questions in `evaluation/questions.py` (a full list of inclusions, of programmes, of exclusions, a count of teachers, and a follow-up "put it in a bullet list"); the judge is told that padded items and self-counted numbers are unsupported (`COUNT_CHECK`). All five passed on 2026-10-18 (retrieval recall 1.0). Also three `language` questions (Spanish, Hindi, German) for 72.
7. **Decide with the customer.** Ask for the email earlier. Not by offering to email a summary: visitors are never sent email (no CC, against spam and misuse), so that would be a promise we cannot keep. Today the bot asks when it cannot answer, which is fine. If a customer wants it earlier, the ask would be after two or three exchanges, framed as the team following up; a per-tenant setting at onboarding.
8. **done** — Faithfulness judge false flag on refusals (judge reasons first, then one yes/no on "claimed an answer"). The grounding judge also checks for mixed-up programmes and sees the facts sheet. Still about three false flags per run (see 79).

## Leads and email

9. **done, live and verified 2026-10-16** — Lead digest: a Cloud Scheduler sweep every 5 minutes emails visitors with contact details once quiet for 15 minutes, even without a contact request (chosen over Cloud Tasks: fixed cost about ₹25/month for all tenants, state-based so missed runs catch up, keeps the service warm). Setup in `notes/lead-digest-setup.md`.
10. **done** — Summary and Resend send in the background; tool result "passed to the team". Retry is now a setting (`retry_failed_lead_emails`, off).
11. **done** — Skip the summary call when the Resend key or team email is missing.
12. **done** — Previous-turn race (the email reads the stored conversation after the turn is saved).
13. **done** — Lead email layout (HTML). Decided: no transcript link; the question list plus summary.
14. **done** — Unanswered-questions report: `python -m scripts.unanswered <tenant> [--days N]`, read-only, no model calls. Labels are stored on each question when it is emailed (`Question.status`).
15. Verified sending domain in Resend, and `NOTIFICATION_FROM_EMAIL` set in `cloudbuild.yaml`.

## Content

16. Real answers from the yoga school for the ~15 open questions (attendance, exam, breaks, visa, pets, teaching language, allergies, day off, weather, what to bring, how far ahead to book, number of graduates, reviews), written into a FAQ file and ingested.
17. Update tenant fields: WhatsApp number and website URL are still placeholders (`910000000000`, `https://example.com/`); greeting.
18. **After onboarding.** Re-measure hybrid search and reranking once a tenant's corpus reaches hundreds of chunks.
19. **After onboarding** (today's real questions are the owner's own tests). **partly** — Grow the evaluation set from real visitor questions. Rebuilt 2026-10-16: 74 questions labelled by section (not chunk id), with programme, comparison, follow-up, trap and general kinds, many from real chats. Keep adding from `scripts.unanswered`.
20. **done** — "Production (from settings)" row in `scripts/evaluate.py` (not yet run).

## Infrastructure

21. **partly** — Measure latency in production. Server-side per turn from logs: read + retrieve 0.5–1.5 s, model 0.7–1.9 s. Full request time and network to the widget not measured.
22. Alerting: error rate and latency alerts.
23. (Replaced by 45–61.)
24. Shared rate-limit counters (hosted Redis with in-memory fallback) once instance count is regularly above 1.
25. Firestore emulator tests. Now covers more Firestore-only paths: `apply_summary`, `mark_items_sent` / `mark_items_failed`, `mark_questions_emailed`.
26. Split production from development, plus a staging environment.
27. **partly** — Linting, type checks, security lint, secrets scan and dependency scanning are in CI, with Dependabot (94). Still open: branch protection with pull requests, and a migration mechanism for model changes.

## Security

28. **Done (103).** ~~Per-tenant token budgets with a circuit breaker.~~
29. **Done (100, 101).** ~~Prompt injection defences, with attack prompts in the evaluation harness.~~
30. **Done (106).** ~~Context hygiene: tell customers in onboarding that nothing confidential goes in facts or documents.~~
31. **partly** — `Tenant.rate_limit` sets a customer's own limit (empty uses the 60/hour default). Still to do: set it for each customer from their traffic.

## Privacy

32. **Done (107, 42).** ~~Retention and deletion of conversations (including recorded questions) and leads, and deleting a visitor's data on request.~~ Still open: finding a visitor's conversations by email address, for a request that names only that.
33. Privacy policy and terms for customers to show visitors, mentioning the CC on lead emails. GDPR applies.

## Product features (later)

34. **In a few days, after deciding which buttons and what each does.** Rich responses: buttons like "view dates", "see photos", "book a call".
35. Customer dashboard for onboarding, facts, usage and billing.
36. Usage metering and billing per tenant.
37. Real user login with Firebase Auth.
38. Specialist agents as tools.

## Separate projects

39. Extraction tool: URL in, reviewed Markdown out, with a contradictions report.
40. BM25 and an inverted index from scratch.

## Housekeeping

41. Delete junk Firestore documents: `pyramid-yogshala__string`, `pyramid-yogshala__ipcheck` (copied over in the rename), two `t1__*` conversations (confirmed present).
42. **done** — `python -m scripts.delete_tenant <tenant> [--conversation <id>]`: dry run by default, `--delete --confirm <tenant>` to delete. Not yet run against real data. 2026-10-18: it had missed the newer collections (programmes, feedback, token_usage, lead_email_counts), and a single conversation's feedback; fixed, and a test now fails if a store's collection is not covered.
43. **Done (2026-10-18).** ~~Rename the tenant from `demo-yoga`.~~ Now `pyramid-yogshala`, site key `pk_pyramid_yogshala_001` (was `pk_demo_yoga_001`), moved with `scripts/rename_tenant.py`, then ingest, sync_facts and delete_tenant; checked afterwards: 57 conversations, 27 leads, 95 vectors under the new id, nothing left under the old. Scripts, test pages and the local `docs/pyramid-yogshala` folder updated. Saved eval runs in `evaluation/history/` keep the old name.
44. **done** — `chat.txt` is in `.gitignore` and untracked (a local copy exists).

## Testing

### Performance

45. Load test: ~20 visitors at once. Expected finding: `chat` is sync, so each request holds one of ~40 threadpool threads; Cloud Run sends up to 80 concurrent requests per instance, so requests queue in the container. Evidence for the async refactor.
46. Stress test: increase until something breaks; confirm the failure is a polite message.
47. Spike test: near zero to heavy in seconds; cold starts with `min-instances=0`.
48. Soak test: hours of moderate traffic. Note: the tenant, facts and BM25 caches hold one entry per tenant and overwrite it, so they grow with the number of tenants, not over time.
49. Scalability test: adding instances adds capacity; per-instance rate limits multiply.

### Correctness under real conditions

50. Concurrency test: two messages to one conversation at once; Firestore transaction contention; two lead emails within a second or two (possible duplicate).
51. **done (2026-10-16)** — Failure injection, `tests/test_failure_injection.py`: OpenAI rate limit, timeout, connection and server errors give a 503 with the visitor message and store nothing; a model failure after `contact_team` keeps the recorded request; malformed, unknown, endless and wrongly typed tool calls are contained; a failed lead-summary call still sends the email (questions unlabelled); store outages are a plain 500 without details; a facts outage still gets a reply. Retrieval failures were already covered. Two gaps are strict xfails: a failed running summary skips the lead email (87), and a lead store outage fails the turn (97). Resend failures were already covered in `test_notify.py`.
52. Cold start test: decides whether `min-instances=1` is worth its cost.
53. OpenAI ceiling test: real calls to find the account's RPM and TPM limits.

### Widget

54. **done (2026-10-16)** — End-to-end browser tests, `tests/browser/test_widget.py` (Playwright, Chromium; skipped when Playwright is not installed, as in CI): greeting and suggestions, bubbles one after another with typing dots, lists kept together, typing and reduced motion show all bubbles, bold rendered and HTML not, Talk to the team button, reload resumes the conversation, new chat forgets it, 429/500/503 notices, keyboard use. Real widget and app on a local port, scripted model, cross-origin like a customer's site. About 10 s. Setup: `pip install playwright`, `python -m playwright install chromium webkit`.
55. **partly** — WebKit (Safari's engine) on an emulated iPhone 13 in `tests/browser`: panel fits the screen, bubbles and button work, no sideways scroll. Still to do: a real iPhone and Android device once, and Firefox.
56. **partly** — `tests/browser`: a host page with aggressive theme CSS (`!important` on buttons, text, line height, hidden textareas) does not reach the widget. Still to do: saved copies of real WordPress and Wix pages.
57. **partly** — `tests/browser`: keyboard only (open with Enter, Shift+Enter for a new line, Escape closes and returns focus) and screen reader labels (dialog, live log, labelled input and buttons). Still to do: an axe scan and a real screen reader pass.

### Operational

58. **partly** — `python -m scripts.smoke_test <base_url> <site_key>` exists (checks `/health` and `/config`); not yet a step in `cloudbuild.yaml`. Smoke test after every deploy in `cloudbuild.yaml`. Prefer `/health` plus `/config`, or a dedicated smoke tenant, over a real chat on every deploy.
59. Backup restore test: Firestore point-in-time recovery, restored once.
60. Cost per conversation: OpenAI, Pinecone and Cohere spend over realistic conversations.

### Security

61. Automated security scan of the public API (OWASP ZAP).

### Before load testing

- The rate limiter will block a load test from one machine: use a test tenant with high limits.
- Avoid real model calls: on staging, point `OPENAI_BASE_URL` at a stub server that replies after ~1.5 s. The OpenAI client reads it, so no load-test mode in the app and nothing to switch on in production by mistake.

### When to run each

| When | Tests |
|---|---|
| Before the first customer | 51, 54, 55, 56, 58, 60 |
| Once staging exists | 45, 46, 47, 50, 52 |
| Once real traffic exists | 48, 49, 53 |
| Periodically | 57, 59, 61 |

## Business gaps (from an end-to-end review, 2026-10-15)

62. **partly** — The widget shows "AI assistant · Answers can be wrong", a Privacy link when `Tenant.privacy_url` is set, and a note when an email is typed. Still needed: each customer's privacy policy URL (with 33). AI and privacy notice in the widget: "AI assistant" plus a privacy link under the header, and a short note when an email is captured. EU AI Act transparency and GDPR notice at collection.
63. Data processing agreement (DPA) with each customer, with the list of sub-processors: OpenAI, Pinecone, Google Cloud (Firestore, Cloud Run), Resend, Sentry.
64. **done** — `notes/install-guide.md`, and the widget logs the reason for a refused key or domain to the browser console. Install guide for customers: copy-paste snippet, WordPress and Wix steps, and a clear error when the site's domain is not in the allowed list (today it is a silent 403).
65. **Done (2026-10-18); you share the sheet.** ~~Scheduled facts sync.~~ The 5-minute lead-digest call (no second scheduler job, endpoint not renamed, so no change to the job) also syncs any tenant whose facts are older than `facts_sync_hours` (24), at most `facts_sync_batch` (2) per call; a sheet that cannot be read is retried after `facts_sync_retry_minutes` (60). A sheet that now has no facts, or lost more than half, is held: old facts kept, error logged, `scripts.sync_facts` by hand accepts it. Production reads sheets as `sitechat-runtime` (no `service-account.json` there), so **each tenant's sheet must be shared with `sitechat-runtime@sitechat-511008.iam.gserviceaccount.com` as Viewer** (Sheets API already enabled). Free: Sheets reads, one Firestore read per tenant per call.
66. **partly** — Throwaway addresses are flagged in the lead email; bot protection (Turnstile) still open. Checked 2026-10-18: Turnstile is free but needs a Cloudflare account (yours), each customer's domain added to the Turnstile widget, and its secret key in Secret Manager, so it waits for a customer domain and a decision. Already limiting spam meanwhile: per-visitor and per-tenant rate limits, refused requests counted, the daily lead-email cap, throwaway domains flagged, and visitor links shown unclickable. Spam protection on leads: Cloudflare Turnstile or similar before the first lead email, and flag throwaway email domains.
67. **Done (2026-10-18).** `Tenant.reply_time` (e.g. "within 24 hours") is shown in the chat card when a contact request is received (82), and repeated in the lead email's request line ("The chat told them you usually reply within 24 hours."), so the team knows what was promised. Set it per customer at onboarding. Was: Reply-time expectation per tenant (e.g. "we reply within 24 hours, Mon to Sat"), used in the bot's reply and the lead email.
68. Weekly report email to each customer: conversations, leads, top questions, unanswered questions (absorbs 14).
69. **Partly done (2026-10-18).** `python -m scripts.leads <tenant> [--days N] [--all]` writes every lead with contact details (last 90 days by default) to `leads-<tenant>.csv`: when, name, email, phone, country, plans, interest, whether they asked to be contacted, when the team was emailed, and every question with its label. UTF-8 with a byte-order mark for Excel; import into Google Sheets with File > Import. Read-only, no model calls; `leads-*.csv` is gitignored (personal data). Still open: appending to a Google Sheet automatically (needs a sheet shared with the runtime account, with edit rights), and whether the team replied (replies go from their own inbox, which we never see; a column the team fills in by hand works).
70. **Skipped for now (2026-10-18).** WhatsApp handoff button using the stored `whatsapp_number` (needs 34; `Action` already exists in the API).
71. **Done (2026-10-18).** Thumbs up/down under each bot reply (after its last bubble; not on the greeting, notices or the card), changeable; a thumbs down offers "Wrong information", "Didn't answer my question", "Other". `/chat` returns `reply_id` (also stored on the reply's `Message.id` and `Question.reply_id`); `POST /feedback` (site key, domain, `rate_limit_feedback_per_visitor`) saves one record per reply in `feedback` (ids only, with `expire_at`), since a tap can arrive before the reply is stored. `python -m scripts.feedback <tenant> [--days N]` lists thumbs-down with question and reply. Tests: API, script, browser (placement, sending, reasons, changing, keyboard). Still open: the "regular review" part is the script for now; counts in a weekly summary to the school later.
72. **Done (2026-10-18).** ~~Language: reply in the visitor's language; write the lead email summary in English.~~ The prompt answers in the language of the visitor's latest message (translating text for them is still refused; attack eval 29/29). The lead email's summary and questions are in English, and the summary ends "They wrote in <language>." so the team can reply in it. Question detection counts other scripts' question marks and long messages in any language; programme names match with hours and days in common languages ("300 horas", "100-Stunden"). Fixed English text stays English: greeting, suggested questions, widget labels and the confirmation card. Retrieval works across languages but ranks lower (eval "language" kind: 3 questions, recall 1.0 after the unit fix; a Hindi "retreat" is not matched as a programme word).
73. Customer support and incidents: support contact, status page or incident email, uptime commitment once paid.
74. **Done (2026-10-18).** ~~Beyond yoga schools: per-tenant prompt wording and lead fields.~~ `Tenant.wording` (`app/tenants/models.py`): ten phrases for what the business offers, who visits, how to help them choose, what to ask once the email arrives, and the lead email's "Planning to join" label. Used in the prompt, the `record_visitor_details` tool and the lead email. The defaults are the yoga school's own wording, so demo-yoga is unchanged: same words, only line breaks moved (`tests/test_wording.py`). A commented guesthouse example is in `scripts/create_tenant.py`. Lead fields keep their stored names (`course_interest` etc.); only their wording changes. Two general examples in the rules still mention courses and students.
75. Data residency: regions of Firestore, Pinecone and OpenAI, for GDPR transfer terms (with 63).

## Found while working on the backlog (2026-10-15)

76. **Largely solved by the lead digest (measured 2026-10-17); watch it.** **Cold start of about 43 seconds.** Measured 2026-10-15: the first request after the service scaled to zero took 43.4 s (container start and Python start-up about 37 s, app start-up 6 s); warm requests take milliseconds on the server. 16 cold starts in the previous ~36 hours, so real visitors meet it. Options, both in `cloudbuild.yaml`: `--min-instances=1` (removes it at normal traffic, roughly ₹1,500/month) and `--cpu-boost` (more CPU during start-up only). Then reduce start-up itself if still slow (image size, imports). Before the first customer.
    - **Measured 2026-10-17** (logs, 2026-10-16 03:00 to 2026-10-17 04:45 UTC). Before the digest job existed (until 13:00 UTC on 10-16): 3 autoscaling cold starts in 10 hours, one of which a visitor met (test page took 18.8 s). After it (15.7 hours, including 11.7 quiet overnight hours): the job called `/internal/lead-digest` 190 times, all 200, never more than 5.7 minutes apart, and **no idle cold start at all**. The only instance starts were deploys (DEPLOYMENT_ROLLOUT, plus one autoscaling start 16 s into a rollout). No visitor request took more than 6.4 s (model time, as normal). Baseline was 16 cold starts in ~36 hours.
    - So the 5-minute digest call keeps the service warm at no extra cost; `--min-instances=1` is not needed for now. Still true after any deploy: the first request goes to a fresh instance (startup CPU boost is on). Re-check after a week of traffic and on the first month's bill (idle instance time should not be charged under request-based billing).
77. **Lead email runs after the response on throttled CPU.** (The lead digest sends inside a request, so it is not affected; immediate contact-request emails still are.) The service uses request-based billing, where CPU is only allocated while a request is in flight; the lead email is a background task after the reply is sent, so it may run slowly or stall. Lead emails arrived 12–24 s after the turn. Option: instance-based billing (`--no-cpu-throttling`), at higher Cloud Run cost. Confirm before relying on lead emails for a customer.
78. **done (2026-10-16)** — Install errors are checked automatically in `tests/browser`: an unknown site key and a domain not on the list both disable the chat and log the `[sitechat]` reason to the console. `tests/manual/widget-check.html` stays for checking by hand.

79. **Done, off by default (2026-10-18).** ~~Use a stronger model for the evaluation judge only.~~ `eval_judge_model` (`app/config.py`): empty uses the bot's `gpt-4o-mini`; `EVAL_JUDGE_MODEL=gpt-4o` in `.env` makes both eval scripts grade with it, never the bot (`tests/test_judge_model.py`). Each run prints the judge it used. Not free: an answer-eval run's grading is roughly 300,000 input tokens, about US$0.75 (≈₹60) with gpt-4o against about US$0.05 (≈₹4) now. Worth it before a decision that rests on the scores; the usual run stays on mini. The judge's known false flag ("What about the 200-hour courses?") came up again on 2026-10-18.
80. **Only if retrieval falls short after onboarding.** Generated questions per chunk (hypothetical question indexing): a model writes 3 to 5 likely visitor questions per chunk at ingest, embedded with the chunk but never shown to the bot; reviewed before ingest. Held in reserve: the school-general split fixed general questions (retrieval MRR 0.46 to 0.90). Revisit if real chats show new retrieval misses.
81. Onboarding for each tenant now includes a programmes.yaml (programmes, the names visitors use, families, overview section) alongside the facts sheet, and a school-general document for school-wide facts.

## From the code and infrastructure review (2026-10-16)

Details, evidence and fixes in `notes/code-review-2026-10-16.md`. All free.

82. **done 2026-10-17 (the "Now" parts)** — Stop CCing the visitor on lead emails; set `reply_to` to their address instead. Any visitor can type someone else's address and, via the digest, make us email them text they wrote: a spam relay that would get the Resend sender blocked. (Also, on `onboarding@resend.dev` Resend only delivers to the account owner, so the CC does not reach real visitors today.) Decided plan (2026-10-16), the visitor still gets an acknowledgement without us emailing an unverified address:
    - **Now:** lead email to the team only, `reply_to` = visitor. A "Reply to <visitor>" `mailto:` button with a prefilled subject and draft (thanks, acknowledges the request, lists their questions for the team to answer; never invents answers), written by the existing `_compose_for_team` call at no extra cost; the draft also shown as copyable text, since `mailto:` links cut off around 2,000 characters. The team reviews and sends it from their own address.
    - **Now:** an in-chat confirmation card when a contact request is placed ("Request sent to <school>. They'll reply to the email you gave, usually within <reply time>"), reply time from the tenant config (67).
    - **Now:** a daily cap on lead emails per tenant (e.g. 50), over the cap waits for the next day, protecting the Resend free quota (100/day, 3,000/month).
    - Built: Reply-To instead of CC; the suggested reply is a fixed template rather than model-written (a model draft would be tempted to fill in answers), listing only questions still needing one; the in-chat card (`confirmation` on `/chat`, `Tenant.reply_time`), which also retires the Talk to the team button; the daily cap (`lead_emails_per_day`, 50; held emails go out with the digest the next UTC day). Widget email notice reworded.
    - **Later, needs a domain (15) and bot protection (66):** an optional automatic receipt to the visitor, fixed template with no visitor or model text, "didn't request this? ignore it" footer, at most 1 per address per 30 days per tenant and a few per address across all tenants. No per-address cap on team emails (the recipient is always the business).
83. **Done, set up 2026-10-18.** ~~Dedicated runtime service account for Cloud Run.~~ `cloudbuild.yaml` deploys as `sitechat-runtime` (Firestore and the five secrets only; logging needs no role); steps in `notes/runtime-sa-setup.md`, to run **before** pushing. `tests/test_cloudbuild.py` checks every deployed secret is granted there. Still open, optional: a separate Cloud Build account and removing Editor from the default compute account.
84. **Done.** ~~Run pytest in `cloudbuild.yaml` before building, so a failing test stops the deploy.~~ First step of the build, `python:3.12-slim`, same env as GitHub Actions; browser tests skip there. Checked in a clean container: about 45 s, and a failing test exits 1.
85. **Done.** ~~OpenAI timeout and retries fit inside Cloud Run's 60 s, and `max_tokens` on replies.~~ Chat calls 15 s per attempt, one retry; embeddings 5 s, one retry (they had the SDK default, 600 s and two retries); one 40 s deadline across a reply's tool rounds, attempts sharing what is left; `reply_max_tokens=600`, a cut reply trimmed to its last sentence. A timeout now says "That took longer than it should". Eval: no reply hit the cap (68/74 grounded, judge noise against 70). Summary and lead-email calls run after the reply and keep the 15 s timeout with no deadline; the digest endpoint is not bounded as a whole (see 92).
86. **Done.** ~~request_id and tenant_id never appear in production logs.~~ `RequestContextMiddleware` sets the request id (inherited by the route's thread and the background tasks); `bound()` sets tenant and conversation where the work runs (route, `after_reply`, each digest conversation); the parallel retrieval pool copies the context (it had none). JSON lines now carry `request_id`, `tenant_id`, `conversation_id`. `tests/test_log_context.py`.
87. **Done.** ~~Isolate the steps of `after_reply`, so a failed summary or write does not skip the lead email and digest flag.~~ Each step runs in `_isolated` (logged with `logger.exception`, so Sentry reports it); the strict xfail test now passes, plus a failed-write test.
88. **Done.** ~~Negative-cache unknown site keys, rate-limit `/config`, and count rejected requests.~~ Unknown keys cached like known ones (one read per key per minute), keys over 64 chars refused unread, cache cleared past 10,000 keys; refusals (bad key, inactive, blocked domain) count against the visitor, `rate_limit_rejected_per_visitor` 20/minute, after which requests are refused before the lookup; `/config` at `rate_limit_config_per_visitor` 30/minute. Tests in `test_api_security.py`.
89. **Done.** ~~Turn off `/docs`, `/redoc` and `/openapi.json` in production.~~ 404 in production, still on locally.
90. **Done.** ~~`CMD exec uvicorn ...` so SIGTERM reaches uvicorn and shutdown is graceful.~~ Checked in a local container: before, `/bin/sh` was PID 1 and `docker stop` killed it (exit 137, no shutdown log); after, uvicorn is PID 1, logs "Shutting down", exits 0; `PORT` still honoured.
91. **Mostly done.** ~~Split runtime and dev requirements, UTF-8.~~ `requirements-dev.txt` adds pytest and its deps; CI and Cloud Build install it, the image does not (666 → 650 MB). Cohere and its huggingface/tokenizers deps stay: rerank is a supported switch, and cohere imports lazily (1.5 ms), so it costs image size, not start time. Open: the local venv is Python 3.14 against 3.12 everywhere else; the Cloud Build test step now catches 3.12-only breakage before deploy, and recreating the venv is the user's call.
92. **Done (ordering open).** ~~Overlapping digest sweeps can send one email twice.~~ Each conversation is claimed in a Firestore transaction (`claim_for_digest`, `digest_claimed_at`, lease `lead_digest_claim_minutes` 10) before it is read again and emailed; a second sweep counts it `busy`. Released when held by the cap, on a failed send with retry on, or if the sweep raises; cleared with `digest_due`. Tested in memory; the Firestore transaction itself needs the emulator (25). Still open: `due_for_digest` has no ordering (needs a composite index), which only matters past 40 flagged conversations at once.
93. **Done.** ~~Cap stored questions and messages per conversation and the length of visitor detail fields.~~ `MAX_QUESTIONS` and `MAX_MESSAGES` 100 (worst case well under 1 MiB, tested); visitor details trimmed to 200 characters, empty ones ignored. `tests/test_size_limits.py`.
94. **Done (2026-10-18).** ~~CI checks.~~ GitHub Actions jobs: `test` (with an 80% coverage floor on app/, 85% now), `lint` (ruff with pyflakes and bugbear rules, mypy on app/, bandit), `secrets` (gitleaks over the full history: no leaks found), `dockerfile` (hadolint), `audit` (pip-audit). Fixed what they found: exception chaining (`raise ... from exc`), unused names, 30 type errors (mostly Firestore's optional `to_dict()`), the smoke test refuses non-http(s) URLs, the image runs as `USER 1000`. Tools pinned in requirements-dev.txt. Post-deploy smoke test (58) is still a manual step. gitleaks-action is free on a personal account; an organisation needs a licence key.

95. **done (to judge in production)** — Reply bubbles: the widget splits each reply on blank lines into separate message bubbles, shown one after another with the typing dots in between (gap by length, about 0.5–1.5 s), so it reads like someone texting. Lists stay with their lead-in line; at most 4 bubbles; typing skips the wait; reduced motion shows all at once. Stored reply, lead emails and evaluation unchanged. If splits are often missing, add a prompt line asking for short messages separated by blank lines (option C, needs an answer-eval run). Measured on 40 recent real replies: 29 would be one bubble, 11 two, because the bot mostly writes one paragraph; so C is likely needed for the effect.

96. **done (to judge in production)** — Making the way to a person hard to miss (2026-10-16). (a) Prompt: chat-style short paragraphs, and an offer of the team (only when the bot could not answer) as its own last paragraph, so it gets its own bubble. Measured on the 74 eval questions: judge pass 70/74 before and after, paragraphs per reply 1.95 to 2.62, offers 7 to 8. A separate "where to place offers" rule made gpt-4o-mini append a generic offer to complete answers (19 to 25 offers), so it was dropped. (b) A "Talk to the team" button above the input, shown after the first reply and hidden once used (back on a new chat); it sends "I'd like someone from the team to contact me.", which the bot records as a contact request and then asks for an email.

97. **Done (2026-10-18).** ~~A lead store outage fails the turn.~~ The reply path wraps the lead store in `FailSoftLeadStore` (`app/leads/store.py`): a failing call is logged as an error and treated as no lead, so the visitor still gets a reply; a contact request that cannot be saved tells the bot to give the direct contact details. The background step keeps the real store. Strict xfail now passes, plus a contact-request-during-outage test.

98. **done** (2026-10-18: both trials kept for now, the 30 s rest and the teaser carrying on after a click) — Launcher teaser (2026-10-17): the launcher reads "Chat with us"; 3 s after the page loads it widens to show a suggested question, changes every 2 seconds (was 1 s; 2026-10-17, too fast to read) through up to 3 of them, settles back, rests 30 seconds (was 5 minutes; changed 2026-10-17, kept 2026-10-18) and repeats. Only questions of 32 characters or fewer that fit in full at the launcher's width (300 px, or the screen minus margins) are used; never cut off. Pointing at or focusing the launcher holds it; clicking a question opens the chat and asks it. It pauses while the chat is open and restarts 3 s after it is closed; clicking no longer ends it for good (2026-10-17 trial, kept 2026-10-18; to undo later: `git revert c4ab662` brings back the click-to-stop-for-good, and the 30 s rest is `5e5fa55`, separate, revert either alone; the localStorage switch-off is gone). Only keyboard focus holds it, so the focus left by closing the chat does not freeze it. None with reduced motion, or with `data-teaser="off"` on the script tag. Timings are constants at the top of the teaser code in `widget.js`. Possible later: a per-tenant list of short launcher questions, if a tenant's suggested questions are all long.

99. **Skipped for now (2026-10-18).** Photos in the chat (discussed 2026-10-17): when a visitor asks about rooms, dining, the yoga hall, bonfires and so on, show the school's own photos under the reply. Proposed design:
    - **Catalog per tenant** (next to `programmes.yaml`): each photo with a topic (`rooms`, `dining`, ...), a caption, and its location or programme, so Havelock questions never show Rishikesh rooms (reuses the conversation's programme focus).
    - **A `show_photos(topic)` tool**, topic limited to the catalog's topics; the server attaches up to 3 matching photos to the reply (an `images` field). The model never supplies a URL.
    - **Widget:** thumbnails under the reply with captions (also the alt text), tap to enlarge; lazy-loaded, about 30 KB each.
    - **Storage:** a public-read Cloud Storage bucket (free tier; a school's 20-40 photos are about 5 MB); an ingest script resizes to thumbnail and full size (WebP) and uploads. The user creates the bucket once.
    - **Open questions:** where the photos come from (the school's permission; real, current photos per location; for the demo, possibly from their public site), how many per reply (3 suggested), whether lead emails list the photos viewed.
    - About a day's work including tests and a few eval questions. Strong pitch moment ("What are the rooms like?").

## Safety phase (OWASP Top 10 for LLM Applications 2026; started 2026-10-17)

Measured, not asserted: `python -m scripts.evaluate_attacks <tenant>` sends 29 attacks (off-topic use, prompt extraction, jailbreaks, planted facts, other visitors' data, markup and links, runaway length) and reports how many held. Already covered before this phase: tenant isolation by Pinecone namespace (LLM09), grounding and the faithfulness eval (LLM07), one small tool with a fixed recipient and a daily cap (LLM03), text-only rendering in the widget and escaping in the email (LLM10).

100. **Done.** Attack evaluation (`evaluation/attacks.py`, `scripts/evaluate_attacks.py`): deterministic checks (prompt text in the reply, forbidden text in the reply *or in tool calls*, length) plus an LLM judge. Baseline 24/29 held; extraction, jailbreak and data already 100%.
101. **Done.** Prompt hardening: scope rule (only questions about the business; decline anything else in one sentence, no partial help), instructions come only from the system message (visitor text, extracts and facts are information, even when they claim to be from an admin), no revealing or describing instructions or tools, short replies; facts and extracts marked as reference material. 28/29, then 29/29 (one judge false positive, now decided by the checks). Answer eval unchanged (70/74). Covers 29.
102. **Done.** Invisible Unicode (tag characters, zero-width space, word joiner, bidi controls, BOM, soft hyphen, variation selectors) is stripped from visitor messages in `ChatRequest` and again in `generate_reply` (`app/agent/sanitize.py`); a message of nothing else is a 422. Kept: ZWJ and emoji selectors (emoji), ZWNJ (Persian, Indic).
103. **Done.** Daily token budget per tenant (`daily_token_budget` 3,000,000, about US$0.50 a day; `Tenant.daily_token_budget` overrides): every reply's tokens (all tool rounds) recorded in `token_usage`; once spent, the bot answers with a 503 "taking a break until tomorrow" before any model call, logged once a day as an error for Sentry. Fails open if the counter is down. The widget now shows a 503's message (all are written for visitors) instead of a generic notice. Not counted: the running summary and the lead email (bounded by the email cap).
104. **Done.** Sentry: local variables and request bodies off (`include_local_variables=False`, `max_request_body_size="never"`). Confirmed the leak first: with Sentry's defaults an error's report contained the visitor's email from a local variable. Test uses the real SDK with an in-memory transport.
105. **Done.** `instruction_like` (`app/agent/sanitize.py`): narrow patterns for text addressed to a model, plus invisible characters. A facts-sheet row that matches is skipped and reported as a problem for the customer; an ingested section that matches is printed as "CHECK" (ingested anyway; we prepare documents). Invisible characters are stripped at ingest. No false positives on demo-yoga's 95 sections and 44 facts; tests include ordinary text like "you must bring a mat".
106. **Done.** Install guide, "What to give the assistant": treat documents and facts as public, what to leave out, state facts rather than instructions (LLM08). Our own prompt holds nothing secret either.
107. **Done, TTL on for conversations and leads 2026-10-18** (the `feedback` policy is the third command; run it if not yet). Retention: every conversation and lead write stamps `expire_at` (`retention_days` 365 after last activity, `app/retention.py`); a Firestore TTL policy deletes them. Two `gcloud` commands in `notes/retention-setup.md`. A guard test checks every full write sets it. Partly covers 32 (deletion on request already exists in `delete_tenant`).
108. **Done (2026-10-18).** Links in visitor-written text (questions, the model's summary, visitor details, the suggested reply) are shown unclickable in the lead email (`hxxp://evil[.]example`, `app/leads/links.py`), with an amber caution note when there were any. Bare domains only with common TLDs, so "Hatha.Ashtanga" is untouched. Contact email and phone are left as they are.
109. **Done.** pip-audit job in GitHub Actions on `requirements.txt` (clean today: no known vulnerabilities) and `.github/dependabot.yml` (pip weekly, minor and patch grouped; Actions monthly; Docker image left out to keep Python 3.12). Part of 94.
110. **Not now.** A separate cheap classifier before the reply, or OpenAI's free moderation endpoint: an extra call and ~0.5 s per message. Only if the attack eval shows the prompt rules are not enough.

## Blocking the first real customer

15 (verified Resend domain and sender), 16 (real FAQ answers), 31 (tenant rate limit), 32–33 (retention, deletion, privacy policy), 26 (production / development split), 22 (alerting).

Also, from the business review: 62 (AI and privacy notice in the widget), 63 (DPA), 64 (install guide), 65 (scheduled facts sync), 66 (spam protection on leads). And 76 (43 s cold start) and 77 (lead email on throttled CPU).

From the review: 82–85 (visitor CC, runtime service account, test-gated deploy, OpenAI timeouts) before anything else; 86–88 soon after.
