"""What visitors thought of the bot's replies: thumbs up/down counts, and
every thumbs-down with the question and the reply.

    python -m scripts.feedback pyramid-yogshala
    python -m scripts.feedback pyramid-yogshala --days 30

Read-only, no model calls. Each thumbs-down is worth a look: a wrong or
missing answer becomes a fix in the documents or facts sheet, then a new
question in evaluation/questions.py (or an attack in evaluation/attacks.py)
so it stays fixed.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.conversations.models import Conversation
from app.conversations.store import ConversationStore, FirestoreConversationStore
from app.feedback.models import Feedback
from app.feedback.store import FeedbackStore, FirestoreFeedbackStore
from app.logging_setup import setup_logging

REASONS = {
    "wrong": "Wrong information",
    "not_answered": "Didn't answer my question",
    "other": "Other",
    None: "no reason given",
}


@dataclass
class Rated:
    feedback: Feedback
    question: str   # "" when no longer stored
    reply: str


def _texts(convo: Conversation | None, reply_id: str) -> tuple[str, str]:
    """The visitor's message and the reply it rated, by reply id."""
    if convo is None:
        return "", ""
    for index, message in enumerate(convo.messages):
        if message.id == reply_id:
            before = convo.messages[index - 1].content if index else ""
            return before, message.content
    # Summarised away: questions are kept longer than messages.
    for question in convo.questions:
        if question.reply_id == reply_id:
            return question.text, question.reply
    return "", ""


def collect(
    feedback_store: FeedbackStore,
    conversations: ConversationStore,
    tenant_id: str,
    since: datetime,
) -> list[Rated]:
    rated, cache = [], {}
    for f in feedback_store.since(tenant_id, since):
        if f.conversation_id not in cache:
            cache[f.conversation_id] = conversations.get(tenant_id, f.conversation_id)
        question, reply = _texts(cache[f.conversation_id], f.reply_id)
        rated.append(Rated(f, question, reply))
    return rated


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tenant_id")
    parser.add_argument("--days", type=int, default=30, help="the last N days (30)")
    args = parser.parse_args(argv)

    setup_logging("WARNING")
    project = get_settings().gcp_project_id
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    rated = collect(FirestoreFeedbackStore(project), FirestoreConversationStore(project),
                    args.tenant_id, since)

    up = sum(r.feedback.rating == "up" for r in rated)
    down = [r for r in rated if r.feedback.rating == "down"]
    print(f"Last {args.days} days: {up} thumbs up, {len(down)} thumbs down")

    for r in down:
        print("\n" + "-" * 60)
        print(f"{r.feedback.updated_at:%Y-%m-%d}  {REASONS[r.feedback.reason]}")
        if not r.reply:
            print("  (reply no longer stored)")
            continue
        print(f"  Visitor: {' '.join(r.question.split())[:300]}")
        print(f"  Bot:     {' '.join(r.reply.split())[:500]}")


if __name__ == "__main__":
    main()
