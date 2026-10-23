"""Splits Markdown text into chunks along its headings.

Why headings rather than a fixed character count: a fixed count cuts
sections in half, so a question about one topic retrieves a fragment that
starts mid-sentence and stops before the useful part. Headings are the
author's own idea of where one topic ends and the next begins.

Very long sections are still split, because a chunk that is too large wastes
context and dilutes the match. But the split happens at paragraph breaks,
and every piece keeps its heading path.
"""

import re

from app.documents.models import Chunk

# A Markdown heading: one to six #, a space, then the text.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Split sections longer than this, measured in characters.
MAX_CHUNK_CHARS = 1800

# Don't leave a trailing fragment smaller than this; merge it back instead.
MIN_CHUNK_CHARS = 200


def _split_long_text(text: str) -> list[str]:
    """Split overlong text at paragraph breaks, never mid-sentence."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    pieces: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > MAX_CHUNK_CHARS and current:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        # A tiny trailing piece is better merged into the one before it.
        if pieces and len(current) < MIN_CHUNK_CHARS:
            pieces[-1] = pieces[-1] + "\n\n" + current
        else:
            pieces.append(current)

    return pieces


def chunk_markdown(
    text: str,
    tenant_id: str,
    document_id: str,
    document_title: str,
) -> list[Chunk]:
    """Turn Markdown into chunks, one per heading section."""
    lines = text.split("\n")

    # Headings currently in scope, as (level, text) pairs.
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    sections: list[tuple[list[str], str]] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(([h[1] for h in heading_stack], body))
        buffer.clear()

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()

            # Drop any headings at this level or deeper: we've left them.
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()

            heading_stack.append((level, title))
        else:
            buffer.append(line)

    flush()

    chunks: list[Chunk] = []
    position = 0

    for heading_path, body in sections:
        for piece in _split_long_text(body):
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}__{position}",
                    tenant_id=tenant_id,
                    document_id=document_id,
                    document_title=document_title,
                    heading_path=heading_path,
                    text=piece,
                    position=position,
                )
            )
            position += 1

    return chunks