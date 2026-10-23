"""Assembles context, calls the model, and stores the result.

This is the single place that defines what a reply is made of. The API layer
calls it and knows nothing about prompts, windowing, retrieval or tools.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app.agent.llm import LLMError, complete, complete_with_tools, tracking_usage
from app.agent.prompts import (
    LEAD_SUMMARY_PROMPT,
    SUMMARY_PROMPT,
    build_context_message,
    build_facts_message,
    build_system_prompt,
)
from app.agent.sanitize import strip_invisible
from app.agent.tools import tool_schemas
from app.config import get_settings
from app.agent.questions import is_visitor_question
from app.conversations.models import REPLY_LIMIT, Conversation, Question
from app.conversations.store import ConversationStore
from app.facts.store import FactStore, get_cached_facts
from app.leads.extraction import extract_contact, looks_like_valid_email
from app.leads.models import AskedQuestion
from app.leads.notify import can_send, send_lead_email
from app.leads.store import LeadStore, fail_soft
from app.programmes.focus import is_comparing, resolve_focus, wants_overview
from app.programmes.store import ProgrammeStore, get_cached_catalogue
from app.request_context import bound
from app.usage.budget import over_budget, record as record_usage
from app.usage.store import UsageStore
from app.retrieval.pipeline import retrieve
from app.tenants.models import Tenant
import contextvars
from concurrent.futures import ThreadPoolExecutor
from app.retrieval.triage import needs_retrieval
from app.leads.pending import ItemKind, PendingItem
import time

logger = logging.getLogger(__name__)

# One tool call plus a final answer is all this needs. The cap exists so a
# model that keeps requesting tools cannot loop indefinitely.
MAX_TOOL_ITERATIONS = 3


def generate_reply(
    tenant: Tenant,
    conversation_id: str,
    user_message: str,
    store: ConversationStore,
    fact_store: FactStore | None = None,
    vector_store=None,
    chunk_store=None,
    lead_store: LeadStore | None = None,
    programme_store: ProgrammeStore | None = None,
    usage_store: UsageStore | None = None,
) -> tuple[str, list, str]:
    lead_store = fail_soft(lead_store)

    # Before any work: once today's budget is spent, nothing more is spent.
    if over_budget(usage_store, tenant):
        raise LLMError(
            "This chat has answered a lot of questions today and is taking a "
            f"break until tomorrow. Please try again then, or contact "
            f"{tenant.display_name} directly."
        )

    # Pull contact details out before the message reaches the model, so they
    # are stored once rather than living in the conversation history and
    # being re-sent on every later turn.
    # Again here (the API already did it) for callers that skip the API,
    # such as the evaluation scripts.
    user_message = strip_invisible(user_message)
    user_message = _capture_contact(
        tenant.tenant_id, conversation_id, user_message, lead_store
    )

    want_retrieval = vector_store is not None and needs_retrieval(user_message)
    catalogue = (
        get_cached_catalogue(programme_store, tenant.tenant_id)
        if programme_store is not None
        else None
    )

    def retrieve_for(focus: list[str]) -> list:
        if not want_retrieval:
            return []
        return _retrieve_safely(
            tenant, conversation_id, user_message, focus, catalogue,
            vector_store, chunk_store,
        )

    # Which programme the message is about (see app/programmes/focus.py).
    # When the message names one without comparing, the earlier focus does
    # not matter, so the conversation read and retrieval run at the same
    # time: both are network-bound. Otherwise retrieval waits for the
    # conversation, to carry its focus over.
    named = resolve_focus(user_message, [], catalogue) if catalogue else []
    t0 = time.monotonic()
    if catalogue is None or (named and not is_comparing(user_message)):
        focus = named
        with ThreadPoolExecutor(max_workers=2) as pool:
            # Pool threads start with an empty context; each task gets a
            # copy of this one, so its log lines keep the request's ids.
            read_future = pool.submit(
                contextvars.copy_context().run,
                store.get, tenant.tenant_id, conversation_id,
            )
            retrieve_future = pool.submit(
                contextvars.copy_context().run, retrieve_for, focus
            )
            convo = read_future.result()
            retrieved = retrieve_future.result()
    else:
        convo = store.get(tenant.tenant_id, conversation_id)
        focus = resolve_focus(user_message, convo.focus if convo else [], catalogue)
        retrieved = retrieve_for(focus)
    t_parallel = time.monotonic() - t0

    if catalogue is not None and focus != (convo.focus if convo else []):
        store.set_focus(tenant.tenant_id, conversation_id, focus)

    history = convo.messages if convo else []
    summary = convo.summary if convo else ""
    
    messages = [{"role": "system", "content": build_system_prompt(tenant)}]

    if summary:
        messages.append(
            {
                "role": "system",
                "content": f"Summary of the earlier conversation: {summary}",
            }
        )

    if fact_store is not None:
        sheet = get_cached_facts(fact_store, tenant.tenant_id)
        facts_message = build_facts_message(sheet)
        if facts_message:
            messages.append({"role": "system", "content": facts_message})

    if lead_store is not None:
        lead = lead_store.get(tenant.tenant_id, conversation_id)
        if lead and lead.has_contact:
            # The message that held their email is redacted, and once older
            # messages are summarised the model loses sight of it and keeps
            # asking again. The lead is the record, so say so every turn.
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The visitor has already given their contact "
                        "details, so do not ask for their email again. If "
                        "they want the team to follow up, call contact_team."
                    ),
                }
            )
        elif lead and lead.has_unsent_items and not lead.has_contact:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "This visitor has asked for something the team needs "
                        "to follow up on, but has not given contact details "
                        "yet. If it fits naturally, offer to have the team "
                        "get in touch and ask for their email. Do not repeat "
                        "the request if they have already declined."
                    ),
                }
            )

    if retrieved:
        context = build_context_message(retrieved)
        if context:
            messages.append({"role": "system", "content": context})

    for message in history:
        messages.append({"role": message.role, "content": message.content})

    messages.append({"role": "user", "content": user_message})

    t0 = time.monotonic()
    with tracking_usage() as tokens:
        try:
            reply = _strip_citation_markers(
                _run_with_tools(messages, tenant, conversation_id, lead_store, user_message)
            )
        finally:
            # Failed calls that got as far as a reply still cost tokens.
            record_usage(usage_store, tenant, tokens[0])
    t_model = time.monotonic() - t0

    logger.info(
        "Timing for %s: read+retrieve %.1fs, model %.1fs",
        conversation_id,
        t_parallel,
        t_model,
    )

    return reply, retrieved, user_message

_CITATION_MARKER = re.compile(r" ?\[\d+(?:\s*,\s*\d+)*\]")


def _strip_citation_markers(reply: str) -> str:
    """Remove "[2]"-style markers. Visitors are not shown sources.

    The prompt no longer asks for them, but the model still copies them
    from older messages in a conversation that began when it did.
    """
    return _CITATION_MARKER.sub("", reply)


def _retrieve_safely(
    tenant: Tenant,
    conversation_id: str,
    message: str,
    focus: list[str],
    catalogue,
    vector_store,
    chunk_store,
) -> list:
    """Retrieve for the message and focus, or nothing if retrieval fails.

    Retrieval calls the embedding API and Pinecone. If either is down, the
    visitor should still get a reply from the facts sheet and the
    conversation, which tell the bot to say what it does not know, rather
    than an error for every message. The failure is logged, so Sentry
    still reports it.
    """
    try:
        return retrieve(
            tenant.tenant_id, message, vector_store, chunk_store,
            programmes=focus or None,
            overview=wants_overview(message, focus, catalogue),
        )
    except Exception:
        logger.exception(
            "Retrieval failed for %s; replying without documents", conversation_id
        )
        return []


def persist_turn(
    tenant_id: str,
    conversation_id: str,
    user_message: str,
    reply: str,
    store: ConversationStore,
    settings,
    reply_id: str = "",
) -> None:
    """Store the turn and summarize if needed.

    Runs after the response has gone out. Nothing here affects what the
    visitor sees, so making them wait for it is pure cost: on a round trip
    to Firestore from outside the region it was adding two to five seconds
    to every message.
    """
    started = time.monotonic()
    store.append_turn(
        tenant_id,
        conversation_id,
        user_message,
        reply,
        is_question=is_visitor_question(user_message),
        reply_id=reply_id,
    )
    _maybe_summarize(tenant_id, conversation_id, store, settings)
    logger.info(
        "Persisted %s in %.1fs", conversation_id, time.monotonic() - started
    )


def _capture_contact(
    tenant_id: str,
    conversation_id: str,
    message: str,
    lead_store: LeadStore | None,
) -> str:
    """Extract and store contact details, returning the redacted message."""
    email, phone, redacted = extract_contact(message)

    if lead_store is None:
        return redacted

    fields = {}
    if email and looks_like_valid_email(email):
        fields["email"] = email
    if phone:
        fields["phone"] = phone

    if fields:
        lead_store.upsert_fields(tenant_id, conversation_id, fields)
        logger.info(
            "Captured contact details for %s: %s",
            conversation_id,
            ", ".join(fields.keys()),
        )

    return redacted


def _run_with_tools(
    messages: list[dict],
    tenant: Tenant,
    conversation_id: str,
    lead_store: LeadStore | None,
    user_message: str,
) -> str:
    """Call the model, handling any tool requests, and return the reply.

    A tool call means the model speaks twice: once to request the tool, and
    again to answer once it has the result. The loop handles that round trip.
    Every round shares one deadline, so the reply fits inside the request.
    """
    conversation = list(messages)
    deadline = time.monotonic() + get_settings().reply_deadline_seconds

    for _ in range(MAX_TOOL_ITERATIONS):
        message = complete_with_tools(
            conversation, tool_schemas(tenant), deadline=deadline
        )

        if not message.tool_calls:
            return message.content or ""

        conversation.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            }
        )

        for tool_call in message.tool_calls:
            result = _execute_tool(
                tool_call.function.name,
                tool_call.function.arguments,
                tenant,
                conversation_id,
                lead_store,
                user_message,
            )
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    logger.warning("Tool loop hit its limit for %s", conversation_id)
    return "I'm having trouble with that one. Could you rephrase it?"


VISITOR_DETAIL_LIMIT = 200


def _execute_tool(
    name: str,
    arguments_json: str,
    tenant: Tenant,
    conversation_id: str,
    lead_store: LeadStore | None,
    user_message: str,
) -> str:
    try:
        arguments = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError:
        arguments = {}

    if name == "record_visitor_details":
        if lead_store is None:
            return "Noted."
        # The model's strings go into Firestore and the lead email, so each
        # is capped: a real name or country is never this long.
        fields = {
            key: value.strip()[:VISITOR_DETAIL_LIMIT]
            for key, value in arguments.items()
            if key in {"name", "country", "intended_month", "course_interest"}
            and isinstance(value, str) and value.strip()
        }
        if fields:
            lead_store.upsert_fields(tenant.tenant_id, conversation_id, fields)
        return "Noted. Do not mention this to the visitor."

    if name == "contact_team":
        # The model tells us why the team is needed. Use that rather than the
        # message that happened to trigger the call: by the time the tool
        # fires, the visitor is often just handing over their email, and the
        # real questions were asked several turns earlier.
        reason = arguments.get("reason", "").strip()
        return _handle_contact_team(
            tenant, conversation_id, lead_store, reason or user_message
        )

    return f"Error: unknown tool '{name}'"


def _handle_contact_team(
    tenant: Tenant,
    conversation_id: str,
    lead_store: LeadStore | None,
    user_message: str,
) -> str:
    """Record the request. The email itself goes out after the reply.

    Nothing is sent here: summarising and sending take seconds, and the
    visitor should not wait for them. notify_team_if_due sends in the
    background, so the bot can only say the request has been passed on.
    """
    unreachable = (
        "Could not reach the team system. Apologise and give the contact "
        "details from the business information you have."
    )
    if lead_store is None:
        return unreachable

    # One waiting request per conversation. The model often calls this
    # again for the same request once the visitor gives their email; the
    # first one already goes out then, so a second would only repeat it.
    lead = lead_store.get(tenant.tenant_id, conversation_id)
    if lead is None or not any(
        item.kind == ItemKind.CONTACT_REQUEST for item in lead.unsent_items
    ):
        # Recorded even if email is not set up, so it goes out once it is.
        lead = lead_store.add_item(
            tenant.tenant_id,
            conversation_id,
            PendingItem(kind=ItemKind.CONTACT_REQUEST, detail=user_message),
        )

    if lead is None or not can_send(tenant):
        # No lead means the lead store failed (logged): the request could
        # not be saved, so the visitor needs the direct contact details.
        return unreachable

    if not lead.has_contact:
        # The item is recorded, so it will go out once we have an address.
        # For now the bot needs to ask for one.
        return (
            "The request is saved and will reach the team as soon as the "
            "visitor gives an email address. Ask them for it. Do not call "
            "contact_team again for this request."
        )

    return (
        "The request has been passed to the team. Tell the visitor someone "
        "will be in touch using the contact details they gave. Do not say "
        "that an email has been sent."
    )


def after_reply(
    tenant: Tenant,
    conversation_id: str,
    user_message: str,
    reply: str,
    store: ConversationStore,
    lead_store: LeadStore | None,
    settings,
    reply_id: str = "",
) -> None:
    """Everything that happens once the reply has gone out, in order.

    The turn is stored first, so the lead email summary includes it. Each
    step runs even if an earlier one failed: a failed summary (a model
    call) or write must not cost the team the visitor's contact request.
    Failures are logged as errors, which Sentry reports.
    """
    with bound(tenant.tenant_id, conversation_id):
        _isolated(
            persist_turn,
            tenant.tenant_id, conversation_id, user_message, reply, store, settings,
            reply_id,
        )
        if lead_store is not None:
            _isolated(notify_team_if_due, tenant, conversation_id, store, lead_store)
            _isolated(
                flag_for_digest, tenant.tenant_id, conversation_id, store, lead_store
            )


def _isolated(step, *args) -> None:
    """Run one step of after_reply, logging (not raising) a failure."""
    try:
        step(*args)
    except Exception:
        logger.exception("after_reply: %s failed", step.__name__)


def request_is_waiting(
    lead_store: LeadStore | None, tenant_id: str, conversation_id: str
) -> bool:
    """Whether a contact request is waiting and the team has a way to reply."""
    if lead_store is None:
        return False
    lead = fail_soft(lead_store).get(tenant_id, conversation_id)
    return bool(
        lead
        and lead.has_contact
        and any(i.kind == ItemKind.CONTACT_REQUEST for i in lead.unsent_items)
    )


def request_confirmation(
    tenant: Tenant,
    lead_store: LeadStore | None,
    conversation_id: str,
    was_waiting: bool,
) -> str | None:
    """The note the widget shows when this turn placed a contact request.

    Shown once, on the turn where a request first has contact details to
    go with it: either the visitor asked and had already given an email,
    or they gave the email after asking. The bot says something similar in
    its own words; this is the reliable, always-the-same version.
    """
    if was_waiting or not request_is_waiting(
        lead_store, tenant.tenant_id, conversation_id
    ):
        return None
    if not can_send(tenant):
        return None
    lead = fail_soft(lead_store).get(tenant.tenant_id, conversation_id)
    if lead is None:
        return None
    where = "the email address you gave" if lead.email else "the phone number you gave"
    when = f", usually {tenant.reply_time}" if tenant.reply_time else ""
    return f"Request received. {tenant.display_name} will reply to {where}{when}."


def flag_for_digest(
    tenant_id: str,
    conversation_id: str,
    store: ConversationStore,
    lead_store: LeadStore,
) -> None:
    """Mark the conversation for the lead digest if it has something to send.

    That is: the visitor has given contact details and asked questions not
    yet emailed. The sweep (send_due_digests) emails them once the visitor
    has been quiet for lead_digest_idle_minutes. Written only when the flag
    changes, so most turns cost two reads and no write.
    """
    lead = lead_store.get(tenant_id, conversation_id)
    if lead is None or not lead.has_contact:
        return
    conversation = store.get(tenant_id, conversation_id)
    if conversation is None or conversation.digest_due:
        return
    if _has_something_to_send(conversation, lead):
        store.set_digest_due(tenant_id, conversation_id, True)


def _has_something_to_send(conversation: Conversation, lead) -> bool:
    """Questions not yet emailed, or a contact request still waiting (one
    the daily cap held back; normally those are emailed at once)."""
    return conversation.questions_emailed < len(conversation.questions) or any(
        item.kind == ItemKind.CONTACT_REQUEST for item in lead.unsent_items
    )


def notify_team_if_due(
    tenant: Tenant,
    conversation_id: str,
    store: ConversationStore,
    lead_store: LeadStore,
) -> None:
    """Email the team if a contact request is waiting and we can reply.

    The email lists every question recorded since the last one (see
    Conversation.questions), so nothing depends on a model noticing them.
    One model call tidies them up, says whether each was answered, and
    writes the summary.

    Within lead_email_cooldown_minutes of the last email, a request with no
    new questions is folded into that email instead of sending another.
    Sending is shared with the digest sweep (_send_lead_email_now).
    """
    lead = lead_store.get(tenant.tenant_id, conversation_id)
    if lead is None or not lead.has_contact:
        return

    items = lead.unsent_items
    if not any(item.kind == ItemKind.CONTACT_REQUEST for item in items):
        return

    # Checked before summarising, so a tenant with no email set up does
    # not pay for a summary nobody receives.
    if not can_send(tenant):
        return

    conversation = store.get(tenant.tenant_id, conversation_id)
    already_emailed = conversation.questions_emailed if conversation else 0
    new_questions = conversation.questions[already_emailed:] if conversation else []

    if not new_questions and _recently_emailed(lead):
        # Nothing the team does not already have from an email minutes ago.
        # Decided without a model call.
        lead_store.mark_items_sent(
            tenant.tenant_id, conversation_id, items, emailed=False
        )
        logger.info(
            "Skipped follow-up email for %s: nothing new since the last one",
            conversation_id,
        )
        return

    _send_lead_email_now(
        tenant, conversation_id, store, lead_store, lead, conversation
    )


def _send_lead_email_now(
    tenant: Tenant,
    conversation_id: str,
    store: ConversationStore,
    lead_store: LeadStore,
    lead,
    conversation: Conversation | None,
) -> bool | None:
    """Email everything new for this conversation, and record it as sent.

    Shared by the immediate email (a contact request) and the digest sweep.
    Lists the questions not yet emailed, with one model call to summarise
    and label them; the lead's waiting items go with it.

    If the send fails, retry_failed_lead_emails decides: on, nothing is
    marked and it is tried again; off, the waiting items are marked failed.
    The questions are never marked emailed on failure, so they go out with
    the next email for this conversation.

    Returns True if sent, False if the send failed, None if held back by
    the daily cap (lead_emails_per_day).
    """
    items = lead.unsent_items
    already_emailed = conversation.questions_emailed if conversation else 0
    new_questions = conversation.questions[already_emailed:] if conversation else []

    # The daily cap, checked before the summary so a held email costs
    # nothing. Held, not dropped: the digest sends it once the day turns.
    day = datetime.now(timezone.utc).date().isoformat()
    cap = get_settings().lead_emails_per_day
    if cap and lead_store.emails_sent_on(tenant.tenant_id, day) >= cap:
        logger.warning(
            "Lead email for %s held: %s reached %d emails today",
            conversation_id, tenant.tenant_id, cap,
        )
        store.set_digest_due(tenant.tenant_id, conversation_id, True)
        return None

    summary, asked = _compose_for_team(conversation, new_questions)

    if send_lead_email(lead, tenant, summary, asked):
        lead_store.count_email_sent(tenant.tenant_id, day)
        if items:
            lead_store.mark_items_sent(tenant.tenant_id, conversation_id, items)
        store.mark_questions_emailed(
            tenant.tenant_id,
            conversation_id,
            already_emailed + len(new_questions),
            # asked[i] is the label for new_questions[i].
            {already_emailed + i: q.status for i, q in enumerate(asked)},
        )
        return True
    if get_settings().retry_failed_lead_emails:
        logger.warning(
            "Lead email failed for %s; left unsent to try again", conversation_id
        )
    else:
        if items:
            lead_store.mark_items_failed(tenant.tenant_id, conversation_id, items)
        logger.warning(
            "Lead email failed for %s; not retried", conversation_id
        )
    return False


def send_due_digests(
    tenant_store,
    store: ConversationStore,
    lead_store: LeadStore,
    now: datetime | None = None,
) -> dict:
    """The lead digest sweep: email conversations that have gone quiet.

    Called every few minutes by Cloud Scheduler (POST /internal/lead-digest).
    For each conversation flagged digest_due whose last activity is at least
    lead_digest_idle_minutes ago, it emails the questions not yet sent, the
    same email as a contact request sends. Works from stored state, so a
    missed or failed run is caught up by the next one, and marking the
    questions emailed means a conversation is never emailed twice.

    At most lead_digest_batch per run, so a run stays well inside the
    request timeout; the rest go next run. Returns counts for the log.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    idle = timedelta(minutes=settings.lead_digest_idle_minutes)
    counts = {"due": 0, "waiting": 0, "sent": 0, "failed": 0, "held": 0,
              "cleared": 0, "busy": 0}

    for conversation in store.due_for_digest(settings.lead_digest_batch * 4):
        if counts["sent"] + counts["failed"] >= settings.lead_digest_batch:
            break
        counts["due"] += 1
        if now - conversation.updated_at < idle:
            counts["waiting"] += 1
            continue

        tenant_id, conversation_id = conversation.tenant_id, conversation.conversation_id
        with bound(tenant_id, conversation_id):
            _digest_one(tenant_store, store, lead_store, settings, counts,
                        tenant_id, conversation_id, now)

    logger.info("Lead digest sweep: %s", counts)
    return counts


def _digest_one(tenant_store, store, lead_store, settings, counts,
                tenant_id, conversation_id, now) -> None:
    """One conversation of the digest sweep; updates counts.

    Claimed first, so a sweep running at the same time skips it (backlog
    92), then read again: what the query returned may predate another
    sweep's email.
    """
    lease = timedelta(minutes=settings.lead_digest_claim_minutes)
    if not store.claim_for_digest(tenant_id, conversation_id, now, lease):
        counts["busy"] += 1
        return
    try:
        _digest_claimed(tenant_store, store, lead_store, settings, counts,
                        tenant_id, conversation_id)
    except Exception:
        # Free it for the next sweep rather than waiting out the lease.
        store.release_digest_claim(tenant_id, conversation_id)
        raise


def _digest_claimed(tenant_store, store, lead_store, settings, counts,
                    tenant_id, conversation_id) -> None:
    conversation = store.get(tenant_id, conversation_id)
    tenant = tenant_store.get_by_id(tenant_id)
    lead = lead_store.get(tenant_id, conversation_id)

    if (
        tenant is None or not tenant.is_active
        or lead is None or not lead.has_contact
        or not _has_something_to_send(conversation, lead)
        or not can_send(tenant)
    ):
        store.set_digest_due(tenant_id, conversation_id, False)
        counts["cleared"] += 1
        return

    sent = _send_lead_email_now(
        tenant, conversation_id, store, lead_store, lead, conversation
    )
    if sent is None:
        # Over today's cap: stays flagged, tried again by later sweeps
        # (no model call while held) and sent once the day turns.
        counts["held"] += 1
        store.release_digest_claim(tenant_id, conversation_id)
        return
    counts["sent" if sent else "failed"] += 1
    if sent or not settings.retry_failed_lead_emails:
        store.set_digest_due(tenant_id, conversation_id, False)  # and the claim
    else:
        store.release_digest_claim(tenant_id, conversation_id)


def _recently_emailed(lead) -> bool:
    """Whether the team was emailed about this lead within the cooldown."""
    minutes = get_settings().lead_email_cooldown_minutes
    if minutes <= 0 or lead.last_notified_at is None:
        return False
    elapsed = datetime.now(timezone.utc) - lead.last_notified_at
    return elapsed < timedelta(minutes=minutes)


def _compose_for_team(
    conversation: Conversation | None, questions: list[Question]
) -> tuple[str, list[AskedQuestion]]:
    """Summarise the conversation and label each question, in one call.

    Every question in `questions` comes back, whatever the model returns:
    one it leaves out or garbles is listed in the visitor's own words as
    "unchecked". The model only tidies and labels; it cannot drop anything.
    """
    fallback = [AskedQuestion(text=q.text) for q in questions]

    if conversation is None or not (conversation.messages or conversation.summary):
        return "No conversation recorded.", fallback

    transcript = "\n".join(f"{m.role}: {m.content}" for m in conversation.messages)
    if conversation.summary:
        transcript = (
            f"Earlier in the conversation: {conversation.summary}\n\n{transcript}"
        )

    numbered = "\n\n".join(
        f"Q{index}: {question.text}\nAssistant replied: {question.reply}"
        # Replies are stored cut to REPLY_LIMIT; say so, so a long answer
        # that was cut off is not mistaken for no answer.
        + (" [shortened]" if len(question.reply) >= REPLY_LIMIT else "")
        for index, question in enumerate(questions, start=1)
    )
    content = f"CONVERSATION:\n{transcript}\n\nQUESTIONS:\n{numbered or '(none)'}"

    try:
        raw = complete(
            [
                {"role": "system", "content": LEAD_SUMMARY_PROMPT},
                {"role": "user", "content": content},
            ],
            json_mode=True,
        )
    except Exception:
        logger.exception("Could not summarise conversation")
        return "(summary unavailable)", fallback

    return _parse_for_team(raw, questions)


QUESTION_STATUSES = {"not_answered", "partly", "answered", "comment"}


def _parse_for_team(
    raw: str, questions: list[Question]
) -> tuple[str, list[AskedQuestion]]:
    """Read the model's JSON, keeping one entry per recorded question."""
    asked = [AskedQuestion(text=q.text) for q in questions]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Lead summary was not JSON; sending it as plain text")
        return raw.strip() or "(summary unavailable)", asked

    if not isinstance(data, dict):
        return "(summary unavailable)", asked

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = "(summary unavailable)"

    entries = data.get("questions")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        match = re.fullmatch(r"Q(\d+)", str(entry.get("id", "")).strip())
        if not match or not 1 <= int(match.group(1)) <= len(asked):
            continue
        index = int(match.group(1)) - 1
        text = entry.get("question")
        status = entry.get("status")
        asked[index] = AskedQuestion(
            text=text.strip() if isinstance(text, str) and text.strip()
            else questions[index].text,
            status=status if status in QUESTION_STATUSES else "unchecked",
        )

    unchecked = sum(1 for a in asked if a.status == "unchecked")
    if unchecked:
        logger.warning(
            "Lead email: %d of %d questions had no valid label", unchecked, len(asked)
        )

    return summary.strip(), asked


def _maybe_summarize(
    tenant_id: str,
    conversation_id: str,
    store: ConversationStore,
    settings,
) -> None:
    """Compress old messages once history grows past the trigger."""
    convo = store.get(tenant_id, conversation_id)
    if not convo or len(convo.messages) <= settings.summarize_trigger:
        return

    overflow = convo.messages[: -settings.history_window]

    transcript = "\n".join(f"{m.role}: {m.content}" for m in overflow)

    prompt = [{"role": "system", "content": SUMMARY_PROMPT}]
    if convo.summary:
        prompt.append(
            {
                "role": "user",
                "content": (
                    f"Existing summary:\n{convo.summary}\n\n"
                    f"New messages to fold in:\n{transcript}"
                ),
            }
        )
    else:
        prompt.append({"role": "user", "content": transcript})

    new_summary = complete(prompt)
    if store.apply_summary(tenant_id, conversation_id, new_summary, overflow):
        logger.info("Summarized conversation %s", conversation_id)
    else:
        logger.info(
            "Skipped summary for %s: history changed underneath it",
            conversation_id,
        )