"""Pure, unit-testable helpers for loading Vietnamese legal documents.

Shared by scripts/load_offline_dataset.py. No DB or network access here.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup


def clean_html(html: str | None) -> str:
    """Convert raw legal-document HTML to normalized plaintext."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    joined = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", joined)
