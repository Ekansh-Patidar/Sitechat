# Sitechat — Project Record

> A complete record of what this project is, how it is built, the decisions
> behind it, what was measured, what failed, and what remains open. Written
> to be handed to an AI assistant or a new engineer so they can pick up
> without re-deriving the reasoning.

---

## 1. What it is

A multi-tenant chat widget that businesses embed on their website with one
script tag. It answers visitor questions from the customer's own documents.

**The real purpose is lead capture, not support.** Answering questions is how
the bot earns the right to ask for contact details. Every conversation should
end with the business knowing who was asking and how to reach them. This
reframing happened partway through the project and changed the design
significantly.

**First customers:** yoga teacher training schools in India, selling 28-day
courses to international students. A high-consideration purchase, where
someone reads for weeks before paying. The bot's job is removing doubt.

**Status:** deployed and working on Cloud Run. One tenant configured with
real content. No paying customers yet.

---

## 2. Current state

### Built and working

| Area | What exists |
|---|---|
| Tenancy | Per-tenant config, site keys, domain allowlist, rate limits |
| Chat | FastAPI `/chat`, conversation storage, windowing, rolling summarization |
| Facts | Structured facts synced from a customer-maintained Google Sheet |
| Documents | Heading-aware chunking, ingestion pipeline, chunk inspector |
| Retrieval | Pinecone vector search with per-tenant namespaces, citations |
| Evaluation | Retrieval metrics (Recall@k, Precision@k, MRR) plus LLM-as-judge faithfulness |
| Leads | PII extraction and redaction, lead records, pending items, email to team |
| Widget | Shadow-DOM embeddable widget, per-tenant accent, dark mode, localStorage sessions |
| Deployment | Docker, Cloud Build CI/CD, Cloud Run, Secret Manager, Sentry |

### Known broken or unreliable

- **Unanswered-question detection.** Does not work. See section 7.
- **The enrichment prompt** (asking for country and intended month after the
  email arrives) fires inconsistently.
- **Citations in the widget UI** are long and noisy; the block is often
  longer than the answer. Should be removed from the UI and kept in logs.

---

## 3. Architecture

### Layering

Everything sits behind interfaces. Each store has an abstract base class, an
in-memory implementation for tests, and a Firestore implementation for
production.

```
app/
├── api/           routes, request/response schemas, dependencies
├── agent/         orchestrator, prompts, tools, LLM wrapper
├── tenants/       tenant model and store
├── conversations/ conversation model and store
├── facts/         structured facts, Google Sheets sync
├── documents/     chunking, loaders, ingestion, chunk store
├── retrieval/     embeddings, vector store, BM25, fusion, reranker, pipeline, triage
├── leads/         lead model, store, PII extraction, pending items, email
├── config.py      settings, validated at startup
└── main.py        app construction and lifespan wiring
```

The orchestrator (`app/agent/orchestrator.py`) assembles context and knows
nothing about where any of it came from. Swapping a backend touches one file.

### Dependency injection

`create_app()` accepts every store as an optional parameter. `lifespan` only
builds real cloud clients when a store was not supplied. Tests pass their
own, so nothing in the test suite touches Google credentials.

This was added after the first CI failure: tests were constructing real
Firestore clients and failing on GitHub Actions, which has no credentials.

### Tenant isolation

Three mechanisms, all structural rather than filter-based:

- **Conversations** use a compound document key, `tenant_id__conversation_id`.
  A lookup with the wrong tenant finds nothing.
- **Pinecone** uses one namespace per tenant. A search is scoped to a
  namespace and cannot see another's data.
- **Chunks, facts and leads** are keyed by tenant.

The reasoning: a filter you must remember to apply is a filter someone
eventually forgets. A structural boundary cannot be forgotten.

### Widget security

The site key is public: it sits in the customer's HTML where anyone can read
it. What protects it is the domain allowlist, checked against the browser's
`Origin` header, which page JavaScript cannot forge.

Honest limitation: this stops browser abuse, not a script with `curl`. Rate
limits are what actually bound the cost exposure.

---

## 4. Key decisions and why

### Facts separated from documents

Descriptive content (curriculum, philosophy, schedule) goes through
retrieval. Anything exact and changeable (prices, dates, inclusions) lives in
a structured facts store and is injected into every prompt directly.

**Why:** the customer's live website listed course dates running "December
2026" then "January 2026". Months going backwards, because someone copied a
block and did not update the years. A bot quoting that would tell a
prospective student about a course that already happened.

Facts sync from a Google Sheet the customer maintains. The sync validates
rows, reports what it cannot parse, and logs every change.

### Google Sheets rather than a web form or WhatsApp bot

Considered a WhatsApp bot for fact updates. Rejected: it needs Meta business
verification, a webhook, a conversational state machine, and per-message
cost, to solve a problem a spreadsheet solves in a day. Revisit if customers
find the sheet annoying.

A web dashboard is the eventual answer, and would write to the same fact
store, so nothing downstream changes.

### Extraction tool kept as a separate project

Turning a customer's website into clean Markdown needs site-specific rules
about 20% of the time. If that lives inside the product, those rules
accumulate in the product's codebase, one per customer.

Decision: a separate tool that takes a URL and produces reviewable Markdown,
plus a report of discrepancies across pages. A human reviews before
ingestion. The product only ever ingests Markdown and does not care where it
came from.

Not yet built.

### No user authentication

Considered email OTP before escalation. Rejected: nothing behind the login is
worth protecting (a visitor's own chat about yoga courses), and the friction
lands at the exact moment the visitor is most valuable.

Sessions resume from `localStorage` with a 30-day expiry and a reset button.
Email validation plus the bot confirming the address back covers the real
risk, which is a typo making a lead unreachable.

### Managed API keys, not customer-supplied

Decided you hold the keys and bill customers, rather than each customer
supplying their own. Simpler, and it keeps control of limits and abuse.

### Single GCP project for now

Production will split from development when the first customer goes live. The
justification: with no customers, a broken deploy costs nothing, and
maintaining two environments while the schema is still moving is friction
without benefit.

### Agent-as-a-tool over multi-agent

If specialist behaviour is needed later, a specialist agent becomes a tool the
main agent can call, roughly 20 lines. True multi-agent coordination is a
different problem and is not needed.

---

## 5. The retrieval measurement

This is the most interesting result in the project.

Built hybrid retrieval (BM25 alongside vector search, merged with Reciprocal
Rank Fusion) and Cohere reranking, because that is the standard advice. Then
built an evaluation harness with 20 hand-written questions, each labelled
with the chunk ids that should be retrieved, and measured.

**Corpus: 28 chunks from one document.**

| Config | Recall | MRR | Semantic MRR |
|---|---|---|---|
| **Vector only** | **0.97** | **0.96** | **0.93** |
| Vector + rerank | 0.91 | 0.90 | 0.83 |
| Hybrid, equal weights | 0.88 | 0.70 | 0.52 |
| Hybrid, keyword at 0.3 | 0.88 | 0.76 | 0.62 |
| Hybrid 0.3 + rerank | 0.91 | 0.90 | 0.83 |

**Both techniques made it worse.**

**Why BM25 hurt:** with 28 chunks, nearly every chunk shares a word with every
query, so BM25's ranking is close to arbitrary. RRF then treats that ranking
as a vote with equal weight to vector search's good ordering. Weighting
keyword at 0.3 helped the ordering but not recall, because the damage happens
before fusion: BM25 pushes correct chunks out of the candidate window
entirely.

**Why reranking hurt:** the chunks are long (300 to 1300 characters) and
topically distinct, which is the easy case for embeddings. Rerankers earn
their place with many short, similar chunks. Here it matched on surface terms
and lost intent: asked "I have never done yoga seriously, is this too
advanced?", it dropped the chunk about who the course suits in favour of one
listing advanced postures, because that chunk contains the word "advanced".

Note also that `vector + rerank` and `hybrid + rerank` scored identically to
two decimal places, which means the reranker was overriding the first stage
entirely.

**Decision:** both kept behind config flags, off by default, with the numbers
recorded in the commit message and a note to re-measure as corpora grow.

**A real bug the harness caught:** the BM25 filter used `if score > 0` to drop
irrelevant chunks. BM25 weights a word by rarity, and with a small corpus the
IDF for a word appearing in roughly half the chunks collapses to exactly
zero. The filter was discarding correct matches. Fixed by filtering on token
overlap instead. This would have been invisible without measurement: it
produces vaguer answers, not errors.

### Faithfulness

Separate script, LLM-as-judge, run on demand. Current result: 16/16 answers
fully grounded, 4/4 unanswerable questions correctly refused.

The one flagged failure was the judge being wrong: it described a correct
refusal in its reasoning and then marked it as not refused. Judges are a
signal, not a verdict, which is why the script prints flagged answers for a
human to read rather than only printing a score.

---

## 6. The latency work

### How the problem was found

Stored timestamps showed 2-second replies. The widget felt slower. The
browser's Network tab showed 6 to 14 seconds.

The discrepancy: `created_at` on a message is set after the model responds, so
it measured only part of the round trip.

Added per-stage timing and found three problems.

### What was wrong

| Stage | Time | Problem |
|---|---|---|
| Firestore writes | 1.8 to 4.7s | Two sequential transactions, running *after* the reply was composed |
| Summarization | up to 7s | An extra model call the visitor waited through |
| Retrieval | ~2s | Fired on every message, including "hi" and "thanks" |
| Pinecone search | 1.4 to 2.6s | Geography: `aws us-east-1` from India |

### What was done

1. **Batched the two writes into one transaction.** They always happen
   together.
2. **Moved writes and summarization to FastAPI `BackgroundTasks`.** Nothing
   there affects what the visitor sees.
3. **Ran the conversation read concurrently with retrieval** using a
   `ThreadPoolExecutor`. They do not depend on each other.
4. **Added triage** (`app/retrieval/triage.py`): short messages matching
   conversational patterns skip retrieval entirely. Anchored patterns, so
   "thanks, but what about the fees?" still retrieves.
5. **Dropped `retrieval_candidates` from 20 to 5**, since keyword search and
   reranking are off and the extra 15 were fetched and discarded.

### Result

11 seconds down to 3 to 6 seconds. "Thank you" came back in 1.7 seconds.

**Known trade-off:** two messages sent in quick succession could have the
second read the conversation before the first's write lands. At human typing
speed this is unlikely, and the latency saving is worth it.

**What remains is mostly geography.** Measuring from India against
`us-central1` and `us-east-1`. On Cloud Run the numbers should be much lower;
this has not been measured in production yet.

---

## 7. What did not work

### Unanswered-question detection — still unsolved

The bot should record every question it cannot answer, so the business learns
what is missing from their material. Three approaches tried, none working.

**Attempt 1: a `record_unanswered` tool.** The model never called it. Across
four unanswerable questions in one real conversation, zero items recorded.
The prompt told it to say it did not know and offer the team, and never
mentioned the tool, so the model did what it was told.

**Attempt 2: strengthen the prompt and the tool description.** The model
started saying *"I will record this as a question that needs to be answered"*
in its replies, twice, while calling nothing. It narrated the action instead
of performing it.

**Attempt 3: regex detection server-side.** Planned but never written
(confirmed in the 2026-10-14 review). The concern, raised correctly, is
brittleness: phrasing varies and the pattern list will never be complete.
What runs today (commit `38631ea`) is attempt 2 again: a stronger prompt
plus the `record_unanswered` tool.

**Options still open:**

- Regex as a cheap first filter, with a classification call to confirm when it
  fires. Reliable without a model call on every message.
- Extract unanswered questions in the email-composition step instead, as
  structured output from the summary call. One call per email instead of one
  per message, with the whole conversation in view. Loses the per-turn
  signal, which may not matter since nothing acts on these until the email
  goes out.

**Chosen (2026-10-15):** the second option. The `record_unanswered` tool and
its prompt rule are gone. The lead email's summary call returns JSON with
the summary and the unanswered questions, read from the whole conversation,
including questions answered with a related but different case. Trade-off:
conversations that never reach an email produce no list; a periodic job over
transcripts would be needed for that.

**Replaced the same day:** that still let the model decide what counted, and
it kept missing questions, worst after older messages were summarised away
(a 16-turn chat lost three of ten). Now the server records every visitor
question with the assistant's reply as each turn is stored
(`Conversation.questions`, never summarised; `is_visitor_question` decides,
no model call). The email's one model call only tidies and labels them by
id (answered / partly / not answered), and the server keeps any it drops.
The email lists every question, answered or not, so the team sees what the
visitor cared about. Pattern: record events deterministically; use the model
to structure them, and validate its output against the record.

**The general lesson:** when something must happen every time, do not ask the
model to remember. The server has the information; let it decide.

### Contact requests used the wrong text

The pending item's detail was set to `user_message`, the message that
triggered the tool call. By the time `contact_team` fires, the visitor is
usually just handing over their email, so the stored detail was "[email
provided]" and the real questions were lost.

Fixed by using the `reason` argument the model already supplies, with the
description rewritten to say who reads it and what they need from it.

### The model inventing content when reformatting

Asked to list what a course covers, after already answering in prose, the
model produced five numbered items. Two were not in the source: "Adjustment
and Alignment" as a separate module, and "Practical Teaching Practice" which
appears nowhere.

Separately, it said shatkarma means "six yogic purification techniques" and
then listed the three the document names.

Both happen when the model restructures rather than answers, and both lost
their citations in the process. A rule was added to `BASE_RULES` against
adding plausible items or stating counts the material does not give.

### The model forgetting intent across turns

Observed in a real transcript: the bot decided to contact the team about
pets, the visitor changed subject to course dates, and the request was never
acted on until the visitor chased it twice. Five minutes between the
intention and the action.

This is what pending items were built to fix. The server now holds the
outstanding request and reminds the model each turn until contact details
arrive.

---

## 8. Debugging notes worth keeping

Problems that cost real time, and what they turned out to be.

**Circular import between `main.py` and `routes.py`.** `main` imported the
router, `routes` imported the limiter from `main`. Fixed by moving the
limiter to its own module, `app/rate_limit.py`.

**`/healthz` returning Google's 404.** Cloud Run's frontend intercepts that
path; requests never reach the container. Renamed to `/health` and it worked
immediately. Diagnosed by noticing the 404 was Google's HTML rather than
FastAPI's `{"detail":"Not Found"}` JSON.

**`'NoneType' object is not callable` with "ASGI 'lifespan' protocol appears
unsupported".** The router and static mount had been registered twice during
an edit. Two mounts on the same path broke the ASGI app.

**`Attribute "app" not found in module "app.main"`.** The module-level
`app = create_app()` line was deleted while removing the duplicate
registrations.

**Secrets permission denied on deploy.** `SENTRY_DSN` was created but its IAM
binding was never run, because the command had a missing space
(`SENTRY_DSN--member=...` parsed as one token).

**Default compute service account did not exist.** GCP creates it lazily when
the Compute Engine API is first enabled.

**Tests making real API calls.** Happened three times. Each time they passed
locally (where `.env` has keys) and failed in CI (where it does not). The
pattern: when adding anything that calls out, ask what the test does when
that service is absent.

**Firestore `set` without `merge=True` wiping fields.** `append_message` wrote
the whole document, silently deleting `todos` and `facts` written moments
earlier by a tool. Only visible against real Firestore; the in-memory test
store has no such behaviour. This is the gap the Firestore emulator tests
would close, and they are still not built.

---

## 9. Open work

### Behaviour

- Unanswered-question detection (section 7)
- Streaming responses, so replies appear word by word
- Remove citations from the widget UI, keep them in logs
- The enrichment prompt firing inconsistently
- Fixed tool-result phrasing instead of relying on model wording

### Features

- **Scheduled lead email**: 30 minutes after the last message, via Cloud
  Tasks, rescheduling on each new message. Needs the deployed service, which
  now exists.
- **Unanswered-questions script**: list what each tenant's bot could not
  answer, as input to improving their knowledge file.
- **Earlier email ask**: after two or three exchanges, offer to send a summary
  of the conversation, as a reason to hand over the address.
- Rich responses: buttons like "view dates" or "see photos". The response
  shape already supports them.

### Infrastructure

- Load testing and the async refactor. Each request currently holds a worker
  thread for its whole duration.
- Privacy: retention, deletion, policy. Mandatory before real visitors'
  conversations accumulate.
- Engineering hygiene: linting in CI, dependency scanning, branch
  protection, staging, migrations.
- Firestore emulator tests.
- Production split from development.

### Security phase

Mapped against OWASP Top 10 for LLM Applications (2026 edition).

Already handled: vector isolation (LLM09), grounding and faithfulness
evaluation (LLM07), minimal agency (LLM03).

Outstanding:

- **Token budgets per tenant with a circuit breaker** (LLM06, Unbounded
  Consumption). Rate limits count requests; one long conversation costs far
  more than twenty short ones.
- **Output handling** (LLM10). The widget already renders model output as
  text, never HTML. Keep it that way.
- **Prompt injection defences** (LLM01), with attack prompts added to the
  evaluation harness so the pass rate is measured rather than asserted.
- **Context hygiene** (LLM08). Assume the system prompt and facts are
  readable. Document in onboarding that nothing confidential goes in them.
- **Data minimisation and retention for leads** (LLM02).

### Housekeeping

- Delete junk Firestore documents: `demo-yoga__string` and two `t1__*`
  conversations from early tests
- A script to remove a tenant's data from Firestore and Pinecone together
- `throttle_rerank` must stay false in production
- Rename the tenant from `demo-yoga`

---

## 10. Resource identifiers

| Item | Value |
|---|---|
| GCP project | `sitechat-511008` |
| Project number | `675621015256` |
| Region | `us-central1` |
| Cloud Run service | `sitechat-api` |
| Service URL | `https://sitechat-api-uukevi3qpq-uc.a.run.app` |
| Artifact Registry | `sitechat` |
| Pinecone index | `sitechat`, namespaces `tenant-<id>` |
| GitHub | `Sandeepdot4/sitechat`, branch `main` |
| Service account | `675621015256-compute@developer.gserviceaccount.com` |
| Tenant | `demo-yoga`, site key `pk_demo_yoga_001` |

**Secrets in Secret Manager:** `OPENAI_API_KEY`, `PINECONE_API_KEY`,
`COHERE_API_KEY`, `RESEND_API_KEY`, `SENTRY_DSN`.

**Health check is `/health`, not `/healthz`.** See section 8.

---

## 11. Working practices

Conventions that emerged and are worth keeping.

**Measure before optimising.** The retrieval result and the latency work both
contradicted the obvious assumption. Both were only visible because something
was measuring.

**Prefer structural guarantees to remembered ones.** Compound keys over
filters, namespaces over `where` clauses, server-side detection over asking
the model to remember.

**Name the trade-off when taking the simpler path.** In-memory stores before
Firestore, HMAC before PyJWT, regex before classification. Each was a
deliberate simplification with the real-world alternative named, not an
oversight.

**Build the measurement before the thing being measured.** The evaluation
harness exists so that "is retrieval good" is a number rather than a feeling.

**Check for unsaved files before testing.** Cost about an hour across the
project, more than once.

**For files changed repeatedly, ask for the whole file.** Describing edits to
a file that has been edited several times produces duplicates and missing
lines. This caused three separate deployment failures in one session.
