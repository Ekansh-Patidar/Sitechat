"""Print a conversation as readable text.

    python -m scripts.dump_conversation pyramid-yogshala
    python -m scripts.dump_conversation pyramid-yogshala > chat.txt
"""

import sys

from google.cloud import firestore

from app.config import get_settings
from app.logging_setup import setup_logging


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.dump_conversation <tenant_id>")
        sys.exit(1)

    tenant_id = sys.argv[1]

    settings = get_settings()
    setup_logging("WARNING")

    db = firestore.Client(project=settings.gcp_project_id)

    query = db.collection("conversations").where(
        filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
    )

    conversations = sorted(
        (doc.to_dict() for doc in query.stream()),
        key=lambda c: c.get("created_at", ""),
    )

    for convo in conversations:
        print("=" * 70)
        print(f"conversation: {convo.get('conversation_id')}")
        print(f"started:      {convo.get('created_at')}")
        print(f"last message: {convo.get('updated_at')}")

        summary = convo.get("summary", "")
        if summary:
            print(f"\nsummary: {summary}")

        print("=" * 70)

        for message in convo.get("messages", []):
            stamp = message.get("created_at", "")
            print(f"\n[{stamp}] {message.get('role')}:")
            print(message.get("content", ""))

        print()


if __name__ == "__main__":
    main()