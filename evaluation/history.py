"""Saves evaluation runs so changes can be compared over time.

Without this, "did that change help" relies on remembering last week's
numbers. A recorded baseline turns it into a comparison.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = Path("evaluation/history")


def save_run(
    tenant_id: str, summaries: dict[str, dict], question_set: str = ""
) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = HISTORY_DIR / f"{tenant_id}-{stamp}.json"

    path.write_text(
        json.dumps(
            {
                "tenant_id": tenant_id,
                "run_at": datetime.now(timezone.utc).isoformat(),
                "question_set": question_set,
                "configs": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_latest(tenant_id: str, question_set: str = "") -> dict | None:
    """Return the most recent previous run on the same question set.

    Scores from a different set of questions are not comparable, so a run
    is only compared with earlier runs of the same set.
    """
    if not HISTORY_DIR.exists():
        return None

    for path in sorted(HISTORY_DIR.glob(f"{tenant_id}-*.json"), reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("question_set", "") == question_set:
            return run
    return None