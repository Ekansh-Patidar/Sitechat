"""Where conversations are stored.

Same interface-plus-two-implementations pattern as the tenant store.

Note that every method takes tenant_id as well as conversation_id. That is
on purpose: a lookup can never return a conversation belonging to another
tenant, even if someone supplies a valid id from elsewhere.
"""

import logging
from abc import ABC, abstractmethod

from google.cloud import firestore

from app.retention import with_expiry

from app.conversations.models import REPLY_LIMIT, Conversation, Message, Question
from datetime import datetime, timedelta, timezone


logger = logging.getLogger(__name__)

COLLECTION = "conversations"


class ConversationStore(ABC):
    @abstractmethod
    def get(self, tenant_id: str, conversation_id: str) -> Conversation | None:
        raise NotImplementedError

    @abstractmethod
    def append(
        self, tenant_id: str, conversation_id: str, role: str, content: str
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply_summary(
        self,
        tenant_id: str,
        conversation_id: str,
        summary: str,
        summarized: list[Message],
    ) -> bool:
        """Set the summary and drop the messages it now covers.

        Summarizing takes seconds, and turns can be appended meanwhile, so
        this removes only `summarized` from the front of the history and
        keeps everything after it. If the history no longer starts with
        `summarized` (another summary got there first), nothing changes
        and it returns False; those messages are summarized next time.
        """
        raise NotImplementedError

    @abstractmethod
    def append_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        user_text: str,
        reply_text: str,
        is_question: bool = False,
        reply_id: str = "",
    ) -> None:
        """Append a user message and a reply in one write.

        Two separate appends means two round trips and two transactions. The
        pair always happens together, so they belong in one write. With
        is_question, the pair is also recorded in `questions`, in the same
        write.
        """
        raise NotImplementedError

    @abstractmethod
    def mark_questions_emailed(
        self,
        tenant_id: str,
        conversation_id: str,
        count: int,
        statuses: dict[int, str] | None = None,
    ) -> None:
        """Record that the first `count` questions have been emailed.

        Never moves backwards, so a slower email cannot undo a later one.
        `statuses` maps a question's index to the label it was emailed with.
        """
        raise NotImplementedError

    @abstractmethod
    def set_focus(
        self, tenant_id: str, conversation_id: str, focus: list[str]
    ) -> None:
        """Record which programme(s) the conversation is about."""
        raise NotImplementedError

    @abstractmethod
    def set_digest_due(
        self, tenant_id: str, conversation_id: str, due: bool
    ) -> None:
        """Mark the conversation as having questions to email, or not."""
        raise NotImplementedError

    @abstractmethod
    def due_for_digest(self, limit: int) -> list[Conversation]:
        """Conversations, across all tenants, marked digest_due."""
        raise NotImplementedError

    @abstractmethod
    def claim_for_digest(
        self, tenant_id: str, conversation_id: str, now: datetime, lease: timedelta
    ) -> bool:
        """Take the conversation for one sweep to email, atomically.

        False if it is no longer due, or another sweep claimed it within
        the lease. Released by set_digest_due or release_digest_claim.
        """
        raise NotImplementedError

    @abstractmethod
    def release_digest_claim(self, tenant_id: str, conversation_id: str) -> None:
        """Give the claim up, leaving the conversation due for a later sweep."""
        raise NotImplementedError


def _claimable(convo: Conversation, now: datetime, lease: timedelta) -> bool:
    if not convo.digest_due:
        return False
    claimed = convo.digest_claimed_at
    return claimed is None or now - claimed >= lease


def _mark_emailed(
    convo: Conversation, count: int, statuses: dict[int, str] | None
) -> None:
    convo.questions_emailed = max(convo.questions_emailed, count)
    for index, status in (statuses or {}).items():
        if 0 <= index < len(convo.questions):
            convo.questions[index].status = status


# Firestore refuses a document over 1 MiB, and a conversation that cannot
# be written loses every later turn. A message is at most ~2,000 characters
# and a question ~2,500 with its reply, so these keep a conversation well
# under half that. Summaries normally keep messages near summarize_trigger;
# the cap only bites if summarizing keeps failing. Questions past the cap
# are not recorded (a scripted visitor, not a real one), and the earliest
# messages give way first.
MAX_QUESTIONS = 100
MAX_MESSAGES = 100


def _add_turn(
    convo: Conversation, user_text: str, reply_text: str, is_question: bool,
    reply_id: str = "",
) -> None:
    convo.messages.append(Message(role="user", content=user_text))
    convo.messages.append(Message(role="assistant", content=reply_text, id=reply_id))
    del convo.messages[:-MAX_MESSAGES]
    # Last activity: the lead digest waits until the visitor has been quiet.
    convo.updated_at = datetime.now(timezone.utc)
    if is_question:
        if len(convo.questions) >= MAX_QUESTIONS:
            logger.warning(
                "Question cap reached for %s; not recording", convo.conversation_id
            )
            return
        convo.questions.append(
            Question(text=user_text, reply=reply_text[:REPLY_LIMIT], reply_id=reply_id)
        )


class InMemoryConversationStore(ConversationStore):
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Conversation] = {}

    def _get_or_create(self, tenant_id: str, conversation_id: str) -> Conversation:
        key = (tenant_id, conversation_id)
        if key not in self._data:
            self._data[key] = Conversation(
                conversation_id=conversation_id, tenant_id=tenant_id
            )
        return self._data[key]

    def get(self, tenant_id: str, conversation_id: str) -> Conversation | None:
        return self._data.get((tenant_id, conversation_id))

    def append(
        self, tenant_id: str, conversation_id: str, role: str, content: str
    ) -> None:
        convo = self._get_or_create(tenant_id, conversation_id)
        convo.messages.append(Message(role=role, content=content))

    def apply_summary(
        self,
        tenant_id: str,
        conversation_id: str,
        summary: str,
        summarized: list[Message],
    ) -> bool:
        convo = self.get(tenant_id, conversation_id)
        if convo is None or convo.messages[: len(summarized)] != summarized:
            return False
        convo.summary = summary
        convo.messages = convo.messages[len(summarized) :]
        return True

    def append_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        user_text: str,
        reply_text: str,
        is_question: bool = False,
        reply_id: str = "",
    ) -> None:
        convo = self._get_or_create(tenant_id, conversation_id)
        _add_turn(convo, user_text, reply_text, is_question, reply_id)

    def mark_questions_emailed(
        self,
        tenant_id: str,
        conversation_id: str,
        count: int,
        statuses: dict[int, str] | None = None,
    ) -> None:
        convo = self.get(tenant_id, conversation_id)
        if convo is not None:
            _mark_emailed(convo, count, statuses)

    def set_focus(
        self, tenant_id: str, conversation_id: str, focus: list[str]
    ) -> None:
        self._get_or_create(tenant_id, conversation_id).focus = list(focus)

    def set_digest_due(
        self, tenant_id: str, conversation_id: str, due: bool
    ) -> None:
        convo = self.get(tenant_id, conversation_id)
        if convo is not None:
            convo.digest_due = due
            convo.digest_claimed_at = None

    def due_for_digest(self, limit: int) -> list[Conversation]:
        return [c for c in self._data.values() if c.digest_due][:limit]

    def claim_for_digest(
        self, tenant_id: str, conversation_id: str, now: datetime, lease: timedelta
    ) -> bool:
        convo = self.get(tenant_id, conversation_id)
        if convo is None or not _claimable(convo, now, lease):
            return False
        convo.digest_claimed_at = now
        return True

    def release_digest_claim(self, tenant_id: str, conversation_id: str) -> None:
        convo = self.get(tenant_id, conversation_id)
        if convo is not None:
            convo.digest_claimed_at = None


class FirestoreConversationStore(ConversationStore):
    """One document per conversation, keyed by tenant and conversation id."""

    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def _doc_id(self, tenant_id: str, conversation_id: str) -> str:
        return f"{tenant_id}__{conversation_id}"

    def get(self, tenant_id: str, conversation_id: str) -> Conversation | None:
        doc_id = self._doc_id(tenant_id, conversation_id)
        doc = self._db.collection(COLLECTION).document(doc_id).get()
        if not doc.exists:
            return None
        return Conversation(**(doc.to_dict() or {}))

    def append(
        self, tenant_id: str, conversation_id: str, role: str, content: str
    ) -> None:
        doc_id = self._doc_id(tenant_id, conversation_id)
        doc_ref = self._db.collection(COLLECTION).document(doc_id)

        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}

            convo = (
                Conversation(**data)
                if data
                else Conversation(
                    conversation_id=conversation_id, tenant_id=tenant_id
                )
            )
            convo.messages.append(Message(role=role, content=content))
            convo.updated_at = datetime.now(timezone.utc)

            transaction.set(doc_ref, with_expiry(convo.model_dump(mode="json")))

        update_in_transaction(self._db.transaction())

    def apply_summary(
        self,
        tenant_id: str,
        conversation_id: str,
        summary: str,
        summarized: list[Message],
    ) -> bool:
        doc_id = self._doc_id(tenant_id, conversation_id)
        doc_ref = self._db.collection(COLLECTION).document(doc_id)

        @firestore.transactional
        def update_in_transaction(transaction) -> bool:
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False

            convo = Conversation(**(snapshot.to_dict() or {}))
            if convo.messages[: len(summarized)] != summarized:
                return False

            convo.summary = summary
            convo.messages = convo.messages[len(summarized) :]
            convo.updated_at = datetime.now(timezone.utc)

            transaction.set(doc_ref, with_expiry(convo.model_dump(mode="json")))
            return True

        return update_in_transaction(self._db.transaction())

    def append_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        user_text: str,
        reply_text: str,
        is_question: bool = False,
        reply_id: str = "",
    ) -> None:
        doc_id = self._doc_id(tenant_id, conversation_id)
        doc_ref = self._db.collection(COLLECTION).document(doc_id)

        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}

            convo = (
                Conversation(**data)
                if data
                else Conversation(
                    conversation_id=conversation_id, tenant_id=tenant_id
                )
            )
            _add_turn(convo, user_text, reply_text, is_question, reply_id)
            convo.updated_at = datetime.now(timezone.utc)

            transaction.set(doc_ref, with_expiry(convo.model_dump(mode="json")))

        update_in_transaction(self._db.transaction())

    def mark_questions_emailed(
        self,
        tenant_id: str,
        conversation_id: str,
        count: int,
        statuses: dict[int, str] | None = None,
    ) -> None:
        doc_id = self._doc_id(tenant_id, conversation_id)
        doc_ref = self._db.collection(COLLECTION).document(doc_id)

        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return
            convo = Conversation(**(snapshot.to_dict() or {}))
            _mark_emailed(convo, count, statuses)
            transaction.set(doc_ref, with_expiry(convo.model_dump(mode="json")))

        update_in_transaction(self._db.transaction())

    def set_focus(
        self, tenant_id: str, conversation_id: str, focus: list[str]
    ) -> None:
        # A merge, so it works before the first turn is stored; the ids are
        # included because a stored conversation must have them.
        doc_id = self._doc_id(tenant_id, conversation_id)
        self._db.collection(COLLECTION).document(doc_id).set(
            with_expiry({
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "focus": list(focus),
            }),
            merge=True,
        )

    def set_digest_due(
        self, tenant_id: str, conversation_id: str, due: bool
    ) -> None:
        doc_id = self._doc_id(tenant_id, conversation_id)
        self._db.collection(COLLECTION).document(doc_id).set(
            {"digest_due": due, "digest_claimed_at": None}, merge=True
        )

    def due_for_digest(self, limit: int) -> list[Conversation]:
        # A single-field equality filter, so Firestore's automatic index
        # serves it; no composite index to create. Few conversations are
        # due at any time, since the flag is cleared once they are sent.
        query = (
            self._db.collection(COLLECTION)
            .where(filter=firestore.FieldFilter("digest_due", "==", True))
            .limit(limit)
        )
        return [Conversation(**(doc.to_dict() or {})) for doc in query.stream()]

    def claim_for_digest(
        self, tenant_id: str, conversation_id: str, now: datetime, lease: timedelta
    ) -> bool:
        doc_ref = self._db.collection(COLLECTION).document(
            self._doc_id(tenant_id, conversation_id)
        )

        # A transaction, so two sweeps reading the same unclaimed document
        # cannot both claim it: the second one's commit fails and is retried
        # against the first one's claim.
        @firestore.transactional
        def claim_in_transaction(transaction) -> bool:
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            if not _claimable(Conversation(**(snapshot.to_dict() or {})), now, lease):
                return False
            transaction.update(doc_ref, {"digest_claimed_at": now})
            return True

        return claim_in_transaction(self._db.transaction())

    def release_digest_claim(self, tenant_id: str, conversation_id: str) -> None:
        self._db.collection(COLLECTION).document(
            self._doc_id(tenant_id, conversation_id)
        ).set({"digest_claimed_at": None}, merge=True)
