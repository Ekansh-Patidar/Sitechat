"""Application settings.

Settings are read once, at startup, from environment variables (or the
local .env file). Pydantic validates them here, so a missing or malformed
value fails immediately with a clear message instead of causing a confusing
error deep inside a request later.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Which environment this process is running in.
    environment: Literal["development", "production"] = "development"

    # Logging verbosity.
    log_level: str = "INFO"

    # Google Cloud project that owns the Firestore database.
    gcp_project_id: str = ""

    # Rate limits, as "count/period" strings understood by slowapi.
    #
    # These are per-instance values: counters live in each process's memory,
    # so every Cloud Run instance counts separately and starts from zero
    # when it starts. At current traffic there is typically one instance,
    # so they are effectively exact. The worst case is 10x under scale-out,
    # bounded by --max-instances=10 in cloudbuild.yaml (50/minute per
    # visitor, 600/hour per tenant). Once the instance count is regularly
    # above 1, move to shared counters, likely a hosted Redis via the
    # limiter's storage_uri with in-memory fallback.
    #
    # The per-tenant limit must be sized to each customer's real traffic
    # before they go live. 60/hour suits a demo, not a busy site. Set it per
    # customer with Tenant.rate_limit; this is the default for tenants
    # without one.
    rate_limit_per_visitor: str = "5/minute"
    rate_limit_per_tenant: str = "60/hour"

    # /config is fetched once per page view, so this is generous; it stops a
    # loop from turning into Firestore reads.
    rate_limit_config_per_visitor: str = "30/minute"

    # Thumbs up/down taps; generous, since a visitor may change their mind.
    rate_limit_feedback_per_visitor: str = "30/minute"

    # Requests refused for a bad site key, an inactive tenant or a domain
    # not on the list. Past this, a visitor's requests are refused before
    # the tenant lookup, so made-up keys cannot run up Firestore reads.
    rate_limit_rejected_per_visitor: str = "20/minute"

    # How many proxies append to X-Forwarded-For in front of us. The visitor
    # is the entry this many from the end. Measured 2026-10-15: direct
    # *.run.app traffic arrives with one entry appended by Google's front
    # end, so 1. Behind an external HTTPS load balancer it would be 2.
    # 0 ignores the header and uses the connection address.
    trusted_proxy_hops: int = 1

    # Caches tenant lookups so Firestore isn't queried on every request.
    tenant_cache_seconds: int = 60

    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    # The model that grades answers in scripts.evaluate_answers and
    # scripts.evaluate_attacks; never the bot. Empty uses openai_model. A
    # stronger judge (e.g. "gpt-4o") makes fewer false flags but costs
    # roughly 15 times as much per run; set it in .env for a run.
    eval_judge_model: str = ""
    # Cloud Run gives a request 60 s (cloudbuild.yaml), so these add up to
    # well under that: embedding at most ~11 s (5 s, one retry), then every
    # model call for the reply (tool rounds included) inside
    # reply_deadline_seconds. A call that would run past the deadline gets a
    # shorter timeout, or fails with a visitor-safe message, never a
    # platform 504. The model usually answers in 1-4 s.
    openai_timeout_seconds: float = 15.0
    openai_max_retries: int = 1
    embedding_timeout_seconds: float = 5.0
    reply_deadline_seconds: float = 40.0

    # Longest reply the model may write, in tokens (~450 words). Replies are
    # meant to be short chat messages; this bounds the time and cost of a
    # runaway one. A reply cut off here is trimmed back to its last whole
    # sentence.
    reply_max_tokens: int = 600

    # Model tokens a tenant's replies may use per day (UTC) before the bot
    # stops answering until midnight UTC; Tenant.daily_token_budget
    # overrides it, 0 here means no budget. A demo reply uses about 4,000
    # to 6,000 tokens, so 3,000,000 is roughly 500 to 700 replies, about
    # US$0.50 a day on gpt-4o-mini. See app/usage/budget.py.
    daily_token_budget: int = 3_000_000

    # How many recent messages to send verbatim.
    history_window: int = 12

    # Summarize older messages once history grows past this.
    summarize_trigger: int = 16
    
    # Path to the service account JSON used to read customer sheets.
    google_service_account_file: str = "service-account.json"

    # Pinecone
    pinecone_api_key: SecretStr = SecretStr("")
    pinecone_index_name: str = "sitechat"

    # Embeddings
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536

    # Retrieval
    retrieval_top_k: int = 5
    
    # Reranking
    cohere_api_key: SecretStr = SecretStr("")
    rerank_model: str = "rerank-v3.5"

    # Relative trust in each retriever during fusion. Keyword search is
    # downweighted because it ranks poorly on small corpora; see fusion.py.
    vector_weight: float = 1.0
    keyword_weight: float = 0.3

    # Throttle rerank calls to stay inside a trial key's limit. Must be False
    # in production, where a shared throttle would make every visitor queue.
    throttle_rerank: bool = True

    # Keyword search is off by default. Measured on a 28-chunk corpus it
    # dropped semantic MRR from 0.93 to 0.62 and recall from 0.97 to 0.88:
    # with few chunks, nearly every one shares a word with the query, so BM25
    # ranks close to randomly and pushes correct chunks out of the top k.
    # This is a scale decision, not a verdict on the technique. Re-run
    # scripts/evaluate.py when a tenant's corpus grows into the hundreds of
    # chunks, and turn it back on if the numbers justify it.
    use_keyword_search: bool = False

    # Reranking is off by default. Measured on a 28-chunk corpus it dropped
    # semantic MRR from 0.93 to 0.83: with long, topically distinct chunks,
    # embeddings already order results well, and the reranker matched on
    # surface terms and lost intent. It overrode the first stage entirely,
    # so hybrid-plus-rerank and vector-plus-rerank scored identically.
    #
    # Rerankers earn their place with many short, similar chunks. Re-run
    # scripts/evaluate.py as corpora grow and turn this back on if justified.
    use_rerank: bool = False

    # Email delivery
    resend_api_key: SecretStr = SecretStr("")
    notification_from_email: str = "onboarding@resend.dev"

    # What happens to a contact request whose lead email fails to send.
    # Off: its items are marked failed and never retried, so one failure
    # costs one summary call and one error. On: they stay waiting, and the
    # send is tried again after every later message in that conversation,
    # including errors that will never succeed (e.g. an unverified Resend
    # sender), each costing a summary call and raising an error.
    retry_failed_lead_emails: bool = False

    # One email per visit. Within this many minutes of the last lead email,
    # a new contact request (often the model calling contact_team again on
    # "thanks, bye") sends another email only if it brings an unanswered
    # question the team does not have yet. Otherwise it is folded into the
    # earlier email. The trade-off: a request with no new question, such as
    # "call me on WhatsApp instead", is not emailed in that window. 0 sends
    # every request.
    lead_email_cooldown_minutes: int = 10

    # The lead digest: once a visitor who has given contact details has been
    # quiet this long, their questions not yet emailed are sent, even if
    # they never asked to be contacted. Swept every 5 minutes by Cloud
    # Scheduler, so the email arrives 15 to 20 minutes after the last
    # message. At most lead_digest_batch emails per sweep.
    lead_digest_idle_minutes: int = 15
    lead_digest_batch: int = 10
    # How long a sweep's claim on a conversation lasts. Far longer than one
    # sweep (60 s at most), so it only matters if a sweep dies mid-email:
    # the conversation is then retried once this has passed.
    lead_digest_claim_minutes: int = 10

    # Facts sync (app/facts/sync.py), run by the same 5-minute sweep: a
    # tenant's facts are re-read from their Google Sheet once older than
    # this, at most facts_sync_batch tenants per run; a failed read is
    # retried after facts_sync_retry_minutes.
    facts_sync_hours: int = 24
    facts_sync_batch: int = 2
    facts_sync_retry_minutes: int = 60

    # At most this many lead emails per tenant per day (UTC), so a flood of
    # fake leads cannot use up the Resend quota (100 a day on the free plan)
    # for every tenant. Over the cap nothing is lost: the conversation is
    # flagged for the digest, which sends it once the day turns. 0 = no cap.
    lead_emails_per_day: int = 50

    # Who may call POST /internal/lead-digest: the service account Cloud
    # Scheduler signs its requests as, and the audience it signs them for.
    # Empty invoker turns the endpoint off.
    lead_digest_invoker: str = ""
    lead_digest_audience: str = "sitechat-lead-digest"

    # Candidates each retriever returns before fusion. With keyword search
    # and reranking off, anything beyond retrieval_top_k is fetched and
    # discarded, so this tracks top_k. Raise it again if either is enabled.
    # The evaluation configs set their own (20, as measured in the notes).
    retrieval_candidates: int = 5

    # Added to the similarity score of sections that belong to the
    # programme the visitor is asking about (see retrieval.pipeline.blend).
    # Big enough to prefer that programme's sections among similar matches,
    # small enough that a clearly better school-wide section still wins.
    # Tuned against the evaluation (2026-10-16): 0.0 lost follow-ups (MRR
    # 0.52), 0.05 still lost some (0.77), 0.08 kept follow-ups at 1.00 and
    # general questions at 0.96, 0.12 and up started losing general ones.
    focus_boost: float = 0.08
    
    # Days a conversation or lead is kept after its last activity, then
    # deleted by Firestore's TTL policy (app/retention.py). 0 keeps them.
    retention_days: int = 365

    # Sentry. Empty disables it, which is the default for local development.
    sentry_dsn: SecretStr = SecretStr("")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, building them only once per process."""
    return Settings()