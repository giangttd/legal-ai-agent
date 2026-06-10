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


_ARTICLE_RE = re.compile(r"(?:^|\n)((?:Điều|ĐIỀU)\s+\d+[a-z]?\.?\s*[^\n]*)", re.MULTILINE)
_ARTNUM_RE = re.compile(r"(?:Điều|ĐIỀU)\s+(\d+[a-z]?)")


def simple_chunk(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
    """Sliding-window chunk with paragraph-boundary snapping; drops tiny chunks."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            newline = text.rfind("\n", start + size - overlap, end + overlap)
            if newline > start:
                end = newline
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)
    return [c for c in chunks if len(c) > 50]


def chunk_document(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[dict]:
    """Split a document by `Điều N`; sub-chunk over-long articles. Falls back to
    plain sliding-window chunking when fewer than 4 articles are found."""
    chunks: list[dict] = []
    articles = list(_ARTICLE_RE.finditer(text))
    if len(articles) > 3:
        for i, match in enumerate(articles):
            start = match.start()
            end = articles[i + 1].start() if i + 1 < len(articles) else len(text)
            article_text = text[start:end].strip()
            num_match = _ARTNUM_RE.match(article_text)
            num = num_match.group(1) if num_match else str(i + 1)
            title = match.group(1).strip()[:200]
            if len(article_text) > chunk_size * 2:
                subs = simple_chunk(article_text, chunk_size, overlap)
                for j, sub in enumerate(subs):
                    chunks.append({
                        "article": f"Điều {num}",
                        "clause": f"phần {j + 1}" if len(subs) > 1 else None,
                        "content": sub,
                        "title": title,
                    })
            else:
                chunks.append({"article": f"Điều {num}", "clause": None,
                               "content": article_text, "title": title})
    else:
        for i, sub in enumerate(simple_chunk(text, chunk_size, overlap)):
            chunks.append({"article": None, "clause": None,
                           "content": sub, "title": f"Phần {i + 1}"})
    return chunks
