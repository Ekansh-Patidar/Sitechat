"""Where feedback is kept: one record per reply, so changing the rating
replaces it rather than adding a second vote.

Only ids are stored, not the reply's text: the reply is written in the
background after it is sent, so a quick tap can arrive before it is. The
review script (scripts/feedback.py) looks the text up by reply id.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from google.cloud import firestore

from app.feedback.models import Feedback
from app.retention import with_expiry

COLLECTION = "feedback"


class FeedbackStore(ABC):
    @abstractmethod
    def save(self, feedback: Feedback) -> None:
        raise NotImplementedError

    @abstractmethod
    def since(self, tenant_id: str, when: datetime) -> list[Feedback]:
        """This tenant's feedback updated at or after `when`, newest first."""
        raise NotImplementedError


def _key(f: Feedback) -> str:
    return f"{f.tenant_id}__{f.conversation_id}__{f.reply_id}"


class InMemoryFeedbackStore(FeedbackStore):
    def __init__(self) -> None:
        self._data: dict[str, Feedback] = {}

    def save(self, feedback: Feedback) -> None:
        self._data[_key(feedback)] = feedback

    def since(self, tenant_id: str, when: datetime) -> list[Feedback]:
        found = [f for f in self._data.values()
                 if f.tenant_id == tenant_id and f.updated_at >= when]
        return sorted(found, key=lambda f: f.updated_at, reverse=True)


class FirestoreFeedbackStore(FeedbackStore):
    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def save(self, feedback: Feedback) -> None:
        self._db.collection(COLLECTION).document(_key(feedback)).set(
            with_expiry(feedback.model_dump(mode="json"))
        )

    def since(self, tenant_id: str, when: datetime) -> list[Feedback]:
        # A single-field equality filter (automatic index); the date is
        # checked here, so no composite index is needed.
        query = self._db.collection(COLLECTION).where(
            filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
        )
        found = [Feedback(**(d.to_dict() or {})) for d in query.stream()]
        found = [f for f in found if f.updated_at >= when]
        return sorted(found, key=lambda f: f.updated_at, reverse=True)
