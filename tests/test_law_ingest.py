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


from scripts._law_ingest import VALID_DOMAINS, detect_domains


def test_detect_domains_finds_labor():
    out = detect_domains("Bộ luật Lao động", "quy định về người lao động và tiền lương")
    assert "lao_dong" in out


def test_detect_domains_defaults_to_other():
    assert detect_domains("Tiêu đề trung tính", "nội dung không khớp") == ["other"]


def test_detect_domains_only_valid_enum_values():
    out = detect_domains("thuế thu nhập doanh nghiệp", "đất đai đầu tư bảo hiểm xã hội")
    assert set(out) <= VALID_DOMAINS


def test_valid_domains_has_twelve_labels():
    assert len(VALID_DOMAINS) == 12
    assert "hanh_chinh" not in VALID_DOMAINS  # the main.py bug must not leak in
