"""Syncs a tenant's facts from their Google Sheet into the fact store.

Logs what changed on every run. A customer changing a price is normal; a
price changing unexpectedly needs to be traceable, and this is the record
that makes that possible.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import get_settings

from app.facts.models import Fact, FactSheet
from app.facts.sheets import read_facts
from app.facts.store import FactStore

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    total: int = 0
    # Not saved: the sheet looked emptied by mistake (scheduled sync only).
    held: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


def _name(key: tuple[str, str]) -> str:
    category, label = key
    return f"{category} / {label}"


def _diff(old: list[Fact], new: list[Fact]) -> SyncResult:
    result = SyncResult(total=len(new))

    # Keyed by category and label: the same label ("Private room") appears
    # under several categories, one per course.
    old_by_key = {(f.category, f.label): f.value for f in old}
    new_by_key = {(f.category, f.label): f.value for f in new}

    for key, value in new_by_key.items():
        name = _name(key)
        if key not in old_by_key:
            result.added.append(f"{name}: {value}")
        elif old_by_key[key] != value:
            result.changed.append(f"{name}: '{old_by_key[key]}' -> '{value}'")

    for key in old_by_key:
        if key not in new_by_key:
            result.removed.append(_name(key))

    return result


def _looks_emptied(old: list[Fact], result: SyncResult) -> bool:
    """A sheet cleared or half deleted by accident, rather than edited."""
    if not old:
        return False
    return result.total == 0 or len(result.removed) > len(old) / 2


def sync_tenant_facts(
    tenant_id: str, sheet_id: str, store: FactStore, careful: bool = False
) -> SyncResult:
    """Read the tenant's sheet and write the result into the fact store.

    careful (the scheduled sync, with nobody watching): a sheet that now
    has no facts, or has lost more than half of them, is not saved; the
    bot keeps the facts it had, and the error is logged for a person to
    check. Run scripts.sync_facts by hand to accept such a change.
    """
    facts, problems = read_facts(sheet_id)

    existing = store.get(tenant_id)
    old_facts = existing.facts if existing else []

    result = _diff(old_facts, facts)
    result.problems = problems

    if careful and _looks_emptied(old_facts, result):
        result.held = True
        logger.error(
            "Facts sync for %s held: the sheet has %d facts, %d removed of %d. "
            "Keeping the old facts; run scripts.sync_facts to accept it.",
            tenant_id, result.total, len(result.removed), len(old_facts),
        )
        return result

    store.save(FactSheet(tenant_id=tenant_id, facts=facts))

    if result.has_changes:
        logger.info(
            "Synced %s: %d added, %d changed, %d removed",
            tenant_id,
            len(result.added),
            len(result.changed),
            len(result.removed),
        )
        for line in result.changed:
            logger.info("  changed: %s", line)
    else:
        logger.info("Synced %s: no changes", tenant_id)

    if problems:
        logger.warning("%s: %d problems in sheet", tenant_id, len(problems))

    return result

# tenant_id -> when this instance last tried a scheduled sync, so a sheet
# that cannot be read is retried hourly, not every sweep.
_attempts: dict[str, datetime] = {}


def sync_stale_facts(
    tenant_store, store: FactStore, now: datetime | None = None
) -> dict:
    """The scheduled part of the facts sync, run by the 5-minute sweep
    (POST /internal/lead-digest): every tenant with a sheet whose facts are
    older than facts_sync_hours is synced, a few per run. Free: Sheets
    reads cost nothing and there are no model calls.

    Never raises: the digest it runs alongside matters more. Returns
    counts for the log.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    stale_after = timedelta(hours=settings.facts_sync_hours)
    retry_after = timedelta(minutes=settings.facts_sync_retry_minutes)
    counts = {"synced": 0, "held": 0, "failed": 0}

    try:
        tenants = tenant_store.with_facts_sheet()
    except Exception:
        logger.exception("Facts sync: could not list tenants")
        return counts

    for tenant in tenants:
        if counts["synced"] + counts["held"] + counts["failed"] >= settings.facts_sync_batch:
            break
        tried = _attempts.get(tenant.tenant_id)
        if tried and now - tried < retry_after:
            continue
        try:
            sheet = store.get(tenant.tenant_id)
            if sheet and now - sheet.updated_at < stale_after:
                continue
            _attempts[tenant.tenant_id] = now
            result = sync_tenant_facts(
                tenant.tenant_id, tenant.facts_sheet_id, store, careful=True
            )
        except Exception:
            # Most often: the sheet is not shared with the service's account.
            logger.exception("Facts sync failed for %s", tenant.tenant_id)
            counts["failed"] += 1
            continue
        counts["held" if result.held else "synced"] += 1

    return counts
