"""Naming and matching sections of the knowledge base.

A section key is "<document_id> | <headings below the title>", for example
"pyramid-yogshala-website | 300-Hour ... (Rishikesh) > Fees". Keys come from
document headings, so they survive re-chunking. Used to tag chunks with the
programme they belong to, and by the evaluation to label answers.

Labels that match keys:
  "doc | Heading > Sub"   that section or anything below it
  "=doc | Heading"        exactly that section, not its sub-sections
  "doc |"                 anywhere in that document
"""


def section_key(document_id: str, heading_path: list[str]) -> str:
    """The key for a chunk: its document and headings, without the title."""
    return f"{document_id} | {' > '.join(heading_path[1:])}"


def matches(key: str, label: str) -> bool:
    if label.startswith("="):
        return key == label[1:]
    if label.endswith("|"):
        return key.startswith(label)
    return key == label or key.startswith(label + " > ")
