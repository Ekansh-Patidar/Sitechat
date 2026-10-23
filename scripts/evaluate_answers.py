"""Generate real answers for the evaluation set and judge them.

    python -m scripts.evaluate_answers pyramid-yogshala

This makes two model calls per question, one to answer and one to judge, so
it is slower and costs more than scripts/evaluate.py. Run it when the prompt
or the knowledge base changes, not on every tweak.
"""

import sys
import uuid

from app.agent.orchestrator import generate_reply
from app.agent.prompts import build_facts_message
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.documents.store import FirestoreChunkStore
from app.facts.store import FirestoreFactStore
from app.logging_setup import setup_logging
from app.programmes.store import FirestoreProgrammeStore
from app.retrieval.vector_store import PineconeVectorStore
from app.tenants.store import FirestoreTenantStore
from evaluation.faithfulness import (
    COUNT_CHECK,
    LANGUAGE_NOTE,
    judge_grounded,
    judge_model,
    judge_refusal,
)
from evaluation.questions import QUESTIONS


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.evaluate_answers <tenant_id>")
        sys.exit(1)

    tenant_id = sys.argv[1]

    settings = get_settings()
    setup_logging("WARNING")

    tenant = FirestoreTenantStore(settings.gcp_project_id).get_by_id(tenant_id)
    if tenant is None:
        print(f"No tenant with id {tenant_id}")
        sys.exit(1)

    vector_store = PineconeVectorStore()
    chunk_store = FirestoreChunkStore(settings.gcp_project_id)
    fact_store = FirestoreFactStore(settings.gcp_project_id)
    # The judge sees the same facts the bot does, so a price quoted from
    # the facts sheet is not marked unsupported.
    facts_message = build_facts_message(fact_store.get(tenant_id))
    programme_store = FirestoreProgrammeStore(settings.gcp_project_id)

    results = []

    for index, question in enumerate(QUESTIONS, start=1):
        print(f"  {index}/{len(QUESTIONS)}  {question.question}")

        # A fresh conversation each time, so one answer cannot influence
        # the next through history.
        conversation_store = InMemoryConversationStore()
        conversation_id = str(uuid.uuid4())

        # Follow-ups: play the earlier messages first, so the bot has the
        # same context a real conversation would give it.
        for earlier in question.history:
            reply, _, stored = generate_reply(
                tenant, conversation_id, earlier, conversation_store,
                fact_store, vector_store, chunk_store,
                programme_store=programme_store,
            )
            conversation_store.append_turn(
                tenant.tenant_id, conversation_id, stored, reply
            )

        answer, retrieved, _ = generate_reply(
            tenant,
            conversation_id,
            question.question,
            conversation_store,
            fact_store,
            vector_store,
            chunk_store,
            programme_store=programme_store,
        )

        if question.kind == "unanswerable":
            result = judge_refusal(question.question, answer)
        else:
            result = judge_grounded(
                question.question,
                answer,
                [r.to_searchable_text_for_rerank() for r in retrieved],
                facts=facts_message,
                history=question.history,
                note={"list": COUNT_CHECK, "language": LANGUAGE_NOTE}.get(question.kind, ""),
            )

        results.append(result)

    grounded = [r for r in results if r.kind == "grounded"]
    refusals = [r for r in results if r.kind == "refusal"]

    print("\n" + "=" * 70)
    print(f"Judge:     {judge_model()}")
    if grounded:
        passed = sum(1 for r in grounded if r.passed)
        print(f"Grounded:  {passed}/{len(grounded)} answers fully supported")
    if refusals:
        passed = sum(1 for r in refusals if r.passed)
        print(f"Refused:   {passed}/{len(refusals)} unanswerable questions refused")

    failures = [r for r in results if not r.passed]
    if not failures:
        print("\nNo problems found.")
        return

    print("\n" + "=" * 70)
    print(f"Flagged ({len(failures)}) — read these yourself, the judge can be wrong")
    print("=" * 70)

    for result in failures:
        label = "NOT REFUSED" if result.kind == "refusal" else "UNSUPPORTED"
        print(f"\n[{label}] {result.question}")
        print(f"  answer: {result.answer[:300]}")
        print(f"  judge:  {result.detail}")
        for claim in result.unsupported_claims:
            print(f"    - {claim}")


if __name__ == "__main__":
    main()