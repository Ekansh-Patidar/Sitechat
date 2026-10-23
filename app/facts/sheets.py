"""Reads a tenant's facts from a Google Sheet.

The sheet is expected to have three columns, with headers in row 1:
Category, Label, Value.

Customers edit these sheets themselves, so malformed rows are normal.
Anything invalid is reported and skipped rather than imported as a blank,
because a fact with an empty value is worse than a missing fact: the model
would state it confidently.
"""

import logging
import os

import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.agent.sanitize import instruction_like
from app.config import get_settings
from app.facts.models import Fact

logger = logging.getLogger(__name__)

# Read-only. The service account can never modify a customer's sheet.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Columns A to C, from row 1 down. The sheet name is left out so this works
# whatever the customer named their first tab.
RANGE = "A1:C1000"


class SheetError(Exception):
    """Raised when a sheet cannot be read or is not in the expected shape."""


def _get_service():
    """Locally, the service account file in .env; on Cloud Run, which has
    no file, the service's own account (sitechat-runtime). Each tenant's
    sheet must be shared, view only, with whichever account reads it."""
    settings = get_settings()
    if os.path.exists(settings.google_service_account_file):
        credentials = service_account.Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
    else:
        credentials, _ = google.auth.default(scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials)


def read_facts(sheet_id: str) -> tuple[list[Fact], list[str]]:
    """Read facts from a sheet.

    Returns the valid facts and a list of human-readable problems found,
    so the caller can show the customer what to fix.
    """
    try:
        service = _get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=RANGE)
            .execute()
        )
    except Exception as exc:
        raise SheetError(f"Could not read sheet {sheet_id}: {exc}") from exc

    rows = result.get("values", [])
    if not rows:
        raise SheetError("The sheet is empty.")

    header = [cell.strip().lower() for cell in rows[0]]
    expected = ["category", "label", "value"]
    if header[:3] != expected:
        raise SheetError(
            f"Expected columns {expected} in row 1, found {header[:3]}."
        )

    facts: list[Fact] = []
    problems: list[str] = []

    for index, row in enumerate(rows[1:], start=2):
        # Rows can be short if trailing cells are empty.
        category = row[0].strip() if len(row) > 0 else ""
        label = row[1].strip() if len(row) > 1 else ""
        value = row[2].strip() if len(row) > 2 else ""

        if not label and not value and not category:
            continue  # Blank spacer row, ignore silently.

        if not label:
            problems.append(f"Row {index}: missing Label, skipped.")
            continue
        if not value:
            problems.append(f"Row {index}: '{label}' has no Value, skipped.")
            continue
        # The sheet is edited by the customer, and every value goes into the
        # bot's context. A row that addresses the bot instead of stating a
        # fact is skipped, so it never reaches the model.
        found = instruction_like(" ".join((category, label, value)))
        if found:
            problems.append(
                f"Row {index}: '{label}' reads like an instruction to the "
                f"assistant ({found!r}), skipped."
            )
            continue

        facts.append(
            Fact(
                category=category or "General",
                label=label,
                value=value,
            )
        )

    return facts, problems