from scripts._law_ingest import clean_html


def test_clean_html_strips_tags_and_scripts():
    html = (
        '<table class="detailcontent"><tr><td>'
        "<div>Điều 1. Phạm vi</div>"
        "<script>alert(1)</script><style>.x{}</style>"
        "<p>Nội dung điều luật.</p></td></tr></table>"
    )
    out = clean_html(html)
    assert "Điều 1. Phạm vi" in out
    assert "Nội dung điều luật." in out
    assert "alert" not in out
    assert "<" not in out


def test_clean_html_empty():
    assert clean_html("") == ""
    assert clean_html(None) == ""


from scripts._law_ingest import chunk_document, simple_chunk


def test_simple_chunk_overlap_and_min_length():
    text = "x" * 4000
    chunks = simple_chunk(text, size=1500, overlap=200)
    assert len(chunks) >= 2
    assert all(len(c) > 50 for c in chunks)


def test_chunk_document_splits_by_article():
    text = "\n".join(f"Điều {i}. Tiêu đề {i}\nNội dung điều {i}." for i in range(1, 6))
    chunks = chunk_document(text)
    assert len(chunks) == 5
    assert chunks[0]["article"] == "Điều 1"
    assert "Nội dung điều 1." in chunks[0]["content"]


def test_chunk_document_no_articles_falls_back():
    text = "Một đoạn văn bản dài không có điều khoản. " * 60
    chunks = chunk_document(text)
    assert len(chunks) >= 1
    assert chunks[0]["article"] is None
