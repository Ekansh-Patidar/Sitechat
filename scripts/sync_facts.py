"""Sync one tenant's facts from their Google Sheet.

Run from the project root:

    python -m scripts.sync_facts pyramid-yogshala
"""

import logging
import sys

from app.config import get_settings
from app.facts.store import FirestoreFactStore
from app.facts.sheets import SheetError
from app.facts.sync import sync_tenant_facts
from app.logging_setup import setup_logging
from app.tenants.store import FirestoreTenantStore

logger = logging.getLogger(__name__)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.sync_facts <tenant_id>")
        sys.exit(1)

    tenant_id = sys.argv[1]

    settings = get_settings()
    setup_logging(settings.log_level)

    tenant_store = FirestoreTenantStore(settings.gcp_project_id)
    tenant = tenant_store.get_by_id(tenant_id)

    if tenant is None:
        logger.error("No tenant with id %s", tenant_id)
        sys.exit(1)

    if not tenant.facts_sheet_id:
        logger.error("Tenant %s has no facts_sheet_id set", tenant_id)
        sys.exit(1)

    fact_store = FirestoreFactStore(settings.gcp_project_id)

    try:
        result = sync_tenant_facts(
            tenant_id, tenant.facts_sheet_id, fact_store
        )
    except SheetError as exc:
        logger.error("Sheet problem: %s", exc)
        sys.exit(1)

    print(f"\nSynced {result.total} facts for {tenant_id}")
    if result.added:
        print(f"  Added:   {len(result.added)}")
    if result.changed:
        print(f"  Changed: {len(result.changed)}")
        for line in result.changed:
            print(f"    {line}")
    if result.removed:
        print(f"  Removed: {len(result.removed)}")
    if result.problems:
        print("\n  Problems to report to the customer:")
        for problem in result.problems:
            print(f"    {problem}")


if __name__ == "__main__":
    main()