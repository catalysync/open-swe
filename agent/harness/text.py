"""Small text helpers shared across harness nodes."""

from __future__ import annotations


def content_text(content: object) -> str:
    """Flatten a message's content to plain text. Studio sends content as a list
    of blocks (``[{'type':'text','text':...}]``); join the text blocks rather than
    str()-ing the envelope (which leaked into slugs / PR titles)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
        return " ".join(p for p in parts if p).strip()
    return str(content)
