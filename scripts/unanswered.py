"""List what a tenant's bot could not answer, to improve their FAQ.

    python -m scripts.unanswered pyramid-yogshala
    python -m scripts.unanswered pyramid-yogshala --days 30

Read-only, and no model calls: it uses the questions recorded on each
conversation and the labels they were given when emailed. Questions from
conversations that never reached an email have no label; they are listed
separately with the bot's reply, to judge by eye.
"""

import argparse
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

from app.config import get_settings
from app.conversations.models import Conversation, Question
from app.logging_setup import setup_logging

NEEDS_WORK = ("not_answered", "partly")


def collect(
    conversations: list[Conversation], since: datetime | None = None
) -> dict[str, list[Question]]:
    """Questions grouped as not_answered, partly, and unlabelled."""
    groups: dict[str, list[Question]] = {
        "not_answered": [], "partly": [], "unlabelled": []
    }
    for convo in conversations:
        for question in convo.questions:
            if since and question.asked_at < since:
                continue
            if question.status in NEEDS_WORK:
                groups[question.status].append(question)
            elif not question.status:
                groups["unlabelled"].append(question)
    for questions in groups.values():
        questions.sort(key=lambda q: q.asked_at)
    return groups


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tenant_id")
    parser.add_argument("--days", type=int, help="only the last N days")
    args = parser.parse_args(argv)

    setup_logging("WARNING")
    db = firestore.Client(project=get_settings().gcp_project_id)
    query = db.collection("conversations").where(
        filter=firestore.FieldFilter("tenant_id", "==", args.tenant_id)
    )
    conversations = [Conversation(**doc.to_dict()) for doc in query.stream()]

    since = (
        datetime.now(timezone.utc) - timedelta(days=args.days)
        if args.days else None
    )
    groups = collect(conversations, since)

    titles = {
        "not_answered": "Not answered",
        "partly": "Partly answered",
        "unlabelled": "Not labelled (no email sent), with the bot's reply",
    }
    for key, title in titles.items():
        questions = groups[key]
        print(f"\n{title} ({len(questions)})")
        print("-" * 60)
        for q in questions:
            print(f"  {q.asked_at:%Y-%m-%d}  {q.text}")
            if key == "unlabelled":
                print(f"              -> {' '.join(q.reply.split())[:120]}")


if __name__ == "__main__":
    main()
