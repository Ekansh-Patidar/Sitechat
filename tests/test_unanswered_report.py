"""Tests for the unanswered-questions report's grouping."""

from datetime import datetime, timedelta, timezone

from app.conversations.models import Conversation, Question
from scripts.unanswered import collect

NOW = datetime.now(timezone.utc)


def q(text, status="", days_ago=0):
    return Question(
        text=text, reply="r", status=status, asked_at=NOW - timedelta(days=days_ago)
    )


def convo(*questions):
    return Conversation(conversation_id="c", tenant_id="t1", questions=list(questions))


def test_groups_what_needs_work_and_skips_answered():
    groups = collect([
        convo(q("Pets?", "not_answered"), q("Dates?", "answered")),
        convo(q("Cards?", "partly"), q("okay thanks", "comment"), q("Visa?")),
    ])
    assert [x.text for x in groups["not_answered"]] == ["Pets?"]
    assert [x.text for x in groups["partly"]] == ["Cards?"]
    assert [x.text for x in groups["unlabelled"]] == ["Visa?"]


def test_since_filters_old_questions():
    groups = collect(
        [convo(q("Old?", "not_answered", days_ago=40), q("New?", "not_answered"))],
        since=NOW - timedelta(days=30),
    )
    assert [x.text for x in groups["not_answered"]] == ["New?"]
