"""Structured facts: the exact, current details a tenant must get right.

These are deliberately simple key/value entries rather than a rigid schema.
Different customers care about different things, and a fixed set of columns
would mean a code change every time one of them needs a new field.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Fact(BaseModel):
    # Short label, e.g. "200hr course fee" or "March 2027 dates".
    label: str

    # The exact answer, e.g. "USD 1,450 including accommodation".
    value: str

    # Optional grouping, used to keep the prompt organised.
    category: str = "general"


class FactSheet(BaseModel):
    """All of one tenant's structured facts."""

    tenant_id: str
    facts: list[Fact] = Field(default_factory=list)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )