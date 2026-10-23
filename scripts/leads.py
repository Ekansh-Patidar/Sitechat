"""Every lead as a CSV file, to open in Excel or import into Google Sheets
(File > Import > Upload), for the team to track who they have followed up.

    python -m scripts.leads pyramid-yogshala
    python -m scripts.leads pyramid-yogshala --days 30 --out may.csv
    python -m scripts.leads pyramid-yogshala --all

Read-only, no model calls. By default only leads with an email address or
phone number, from the last 90 days. The file holds visitors' personal
details: keep it out of the repo (leads-*.csv is ignored) and delete it
once imported.

Written as UTF-8 with a byte-order mark, so Excel shows accented names
correctly; the file is written here rather than printed, because PowerShell's
`>` would save it as UTF-16.
"""

import argparse
import csv
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.conversations.store import ConversationStore, FirestoreConversationStore
from app.leads.email_html import STATUS_LABELS
from app.leads.models import Lead
from app.leads.pending import ItemKind
from app.leads.store import FirestoreLeadStore, LeadStore
from app.logging_setup import setup_logging

COLUMNS = [
    "last active (UTC)", "first seen (UTC)", "name", "email", "phone",
    "country", "planning to come", "interested in", "asked to be contacted",
    "emailed to team (UTC)", "questions", "conversation id",
]


def _when(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else ""


def _questions(conversations: ConversationStore, lead: Lead) -> str:
    """Every question the visitor asked, with its label once emailed."""
    convo = conversations.get(lead.tenant_id, lead.conversation_id)
    if convo is None:
        return ""
    # A question is labelled once it has been in a lead email; before
    # that its status is empty.
    return " | ".join(
        f"[{STATUS_LABELS[q.status]}] {_flat(q.text)}"
        if q.status in STATUS_LABELS and q.status != "unchecked" else _flat(q.text)
        for q in convo.questions
    )


def _flat(text: str) -> str:
    """One line per cell, so rows stay readable in a spreadsheet."""
    return " ".join(text.split())


def rows(
    leads: LeadStore,
    conversations: ConversationStore,
    tenant_id: str,
    since: datetime,
    everyone: bool = False,
) -> Iterable[list[str]]:
    for lead in leads.since(tenant_id, since):
        if not everyone and not (lead.email or lead.phone):
            continue
        yield [
            _when(lead.updated_at),
            _when(lead.created_at),
            lead.name,
            lead.email,
            lead.phone,
            lead.country,
            lead.intended_month,
            lead.course_interest,
            "yes" if any(i.kind == ItemKind.CONTACT_REQUEST for i in lead.items) else "",
            _when(lead.last_notified_at),
            _questions(conversations, lead),
            lead.conversation_id,
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tenant_id")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--out", help="file to write (default leads-<tenant>.csv)")
    parser.add_argument("--all", action="store_true",
                        help="include leads with no email or phone")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging("WARNING")
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    out = args.out or f"leads-{args.tenant_id}.csv"

    found = list(rows(
        FirestoreLeadStore(settings.gcp_project_id),
        FirestoreConversationStore(settings.gcp_project_id),
        args.tenant_id, since, args.all,
    ))
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(found)
    print(f"{len(found)} leads from the last {args.days} days written to {out}")


if __name__ == "__main__":
    main()
