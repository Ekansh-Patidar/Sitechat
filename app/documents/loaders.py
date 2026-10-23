"""Turns a source into plain text.

Only this layer changes per format. The chunker, store and retrieval all
work on text, so adding PDF or website support later means adding a
function here and nothing else.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt"}


class LoaderError(Exception):
    pass


def load_text_file(path: Path) -> tuple[str, str]:
    """Read a .md or .txt file. Returns (title, text).

    The title comes from the first Markdown H1 if there is one, otherwise
    from the filename, since that is what a visitor will see as a citation.
    """
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise LoaderError(
            f"{path.name}: unsupported type. Supported: "
            f"{', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LoaderError(f"{path.name}: not valid UTF-8 text") from exc
    except OSError as exc:
        raise LoaderError(f"{path.name}: could not read ({exc})") from exc

    if not text.strip():
        raise LoaderError(f"{path.name}: file is empty")

    title = path.stem.replace("-", " ").replace("_", " ").title()
    for line in text.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return title, text