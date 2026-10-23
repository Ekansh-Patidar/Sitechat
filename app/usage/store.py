"""Model tokens used per tenant per day (UTC), for the daily budget.

One small document per tenant per day, incremented on the server, so two
instances adding at once both count.
"""

from abc import ABC, abstractmethod

from google.cloud import firestore

COLLECTION = "token_usage"


class UsageStore(ABC):
    @abstractmethod
    def tokens_on(self, tenant_id: str, day: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def add_tokens(self, tenant_id: str, day: str, tokens: int) -> None:
        raise NotImplementedError


class InMemoryUsageStore(UsageStore):
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], int] = {}

    def tokens_on(self, tenant_id: str, day: str) -> int:
        return self._data.get((tenant_id, day), 0)

    def add_tokens(self, tenant_id: str, day: str, tokens: int) -> None:
        key = (tenant_id, day)
        self._data[key] = self._data.get(key, 0) + tokens


class FirestoreUsageStore(UsageStore):
    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def _doc(self, tenant_id: str, day: str):
        return self._db.collection(COLLECTION).document(f"{tenant_id}__{day}")

    def tokens_on(self, tenant_id: str, day: str) -> int:
        doc = self._doc(tenant_id, day).get()
        return int((doc.to_dict() or {}).get("tokens", 0)) if doc.exists else 0

    def add_tokens(self, tenant_id: str, day: str, tokens: int) -> None:
        self._doc(tenant_id, day).set(
            {"tenant_id": tenant_id, "day": day, "tokens": firestore.Increment(tokens)},
            merge=True,
        )
