"""How long visitors' data is kept.

Conversations and leads hold personal data (questions, names, email
addresses), so they should not be kept for ever (OWASP LLM02, data
minimisation; backlog 32). Every write stamps the document with expire_at,
retention_days after its last activity, and a Firestore TTL policy on that
field deletes it once the date passes (usually within a day). The policy is
created once per collection; see notes/retention-setup.md. Until it is,
the field is just a date and nothing is deleted.

Firestore only honours a TTL on a real timestamp, so expire_at is set on the
dict being written, after model_dump(mode="json") has turned the model's
own dates into strings.
"""

from datetime import datetime, timedelta, timezone

from app.config import get_settings

FIELD = "expire_at"


def with_expiry(data: dict) -> dict:
    """data with expire_at set, or unchanged if retention is off (0 days)."""
    days = get_settings().retention_days
    if days > 0:
        data[FIELD] = datetime.now(timezone.utc) + timedelta(days=days)
    return data
