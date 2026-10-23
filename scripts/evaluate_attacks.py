"""Send the attack set (evaluation/attacks.py) to the real bot and judge it.

    python -m scripts.evaluate_attacks pyramid-yogshala [category]

Two model calls per attack (the reply and the judge), about 30 attacks: a
few cents a run. Run it after any change to the prompt, the tools, or what
goes into the model's context, and compare the pass rate per category.
"""

import sys
import uuid
from collections import defaultdict

from app.agent.orchestrator import generate_reply
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.documents.store import FirestoreChunkStore
from app.facts.store import FirestoreFactStore
from app.leads.store import InMemoryLeadStore
from app.logging_setup import setup_logging
from app.programmes.store import FirestoreProgrammeStore
from app.retrieval.vector_store import PineconeVectorStore
from app.tenants.store import FirestoreTenantStore
from evaluation.attacks import ATTACKS, judge_attack
from evaluation.faithfulness import judge_model


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.evaluate_attacks <tenant_id> [category]")
        sys.exit(1)
    tenant_id = sys.argv[1]
    only = sys.argv[2] if len(sys.argv) > 2 else None

    # The hidden-text attack's characters do not exist in Windows' console
    # encoding; show them as "?" rather than crash.
    sys.stdout.reconfigure(errors="replace")

    settings = get_settings()
    setup_logging("WARNING")
    tenant = FirestoreTenantStore(settings.gcp_project_id).get_by_id(tenant_id)
    if tenant is None:
        print(f"No tenant with id {tenant_id}")
        sys.exit(1)

    stores = dict(
        fact_store=FirestoreFactStore(settings.gcp_project_id),
        vector_store=PineconeVectorStore(),
        chunk_store=FirestoreChunkStore(settings.gcp_project_id),
        programme_store=FirestoreProgrammeStore(settings.gcp_project_id),
    )
    attacks = [a for a in ATTACKS if only is None or a.category == only]

    results = []
    for index, attack in enumerate(attacks, start=1):
        print(f"  {index}/{len(attacks)}  [{attack.category}] {attack.message[:60]}")
        # Fresh stores each time; leads in memory, so nothing is emailed.
        conversations, leads = InMemoryConversationStore(), InMemoryLeadStore()
        conversation_id = str(uuid.uuid4())
        for earlier in attack.history:
            reply, _, stored = generate_reply(
                tenant, conversation_id, earlier, conversations,
                lead_store=leads, **stores,
            )
            conversations.append_turn(tenant.tenant_id, conversation_id, stored, reply)
        reply, _, _ = generate_reply(
            tenant, conversation_id, attack.message, conversations,
            lead_store=leads, **stores,
        )
        lead = leads.get(tenant.tenant_id, conversation_id)
        tool_text = "" if lead is None else " ".join(
            [item.detail for item in lead.items]
            + [lead.name, lead.country, lead.intended_month, lead.course_interest]
        )
        results.append(judge_attack(attack, reply, tool_text))

    by_category = defaultdict(list)
    for result in results:
        by_category[result.attack.category].append(result)

    print("\n" + "=" * 70)
    print(f"judge        {judge_model()}")
    for category, group in by_category.items():
        passed = sum(r.passed for r in group)
        print(f"{category:<12} {passed}/{len(group)} held")
    total = sum(r.passed for r in results)
    print(f"{'all':<12} {total}/{len(results)} held")

    failures = [r for r in results if not r.passed]
    if not failures:
        return
    print("\n" + "=" * 70)
    print(f"Did not hold ({len(failures)}) - read these yourself, the judge can be wrong")
    print("=" * 70)
    for result in failures:
        print(f"\n[{result.attack.category}] {result.attack.message}")
        print(f"  reply: {result.reply[:400]}")
        for reason in result.reasons:
            print(f"  - {reason}")


if __name__ == "__main__":
    main()
