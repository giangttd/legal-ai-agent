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


def test_valid_domains_labels():
    assert len(VALID_DOMAINS) == 13
    # hanh_chinh is now a real legal_domain enum member (review #4) so the live
    # search path's admin-law queries no longer fail the ::legal_domain[] cast.
    assert "hanh_chinh" in VALID_DOMAINS


def test_detect_domains_admin_law():
    out = detect_domains("Nghị định xử phạt vi phạm hành chính", "mức phạt hành chính")
    assert out == ["hanh_chinh"]


from datetime import date

from scripts._law_ingest import (
    map_law_type_legacy,
    map_law_type_vi,
    map_status_legacy,
    map_status_vi,
    normalize_doc_number,
    parse_date_vi,
)


def test_map_law_type_vi():
    assert map_law_type_vi("Quyết định") == "quyet_dinh"
    assert map_law_type_vi("Thông tư liên tịch") == "thong_tu"
    assert map_law_type_vi("Hiến pháp") == "hien_phap"
    assert map_law_type_vi("Chỉ thị") == "other"
    assert map_law_type_vi(None) == "other"


def test_map_law_type_legacy():
    assert map_law_type_legacy("Decision") == "quyet_dinh"
    assert map_law_type_legacy("Official Dispatch") == "cong_van"
    assert map_law_type_legacy("Circular") == "thong_tu"
    assert map_law_type_legacy("Kế hoạch") == "other"


def test_map_status_vi():
    assert map_status_vi("Còn hiệu lực") == "active"
    assert map_status_vi("Hết hiệu lực toàn bộ") == "expired"
    assert map_status_vi("Hết hiệu lực một phần") == "amended"
    assert map_status_vi("") == "active"


def test_map_status_legacy():
    assert map_status_legacy("In effect") == "active"
    assert map_status_legacy("Expired") == "expired"
    assert map_status_legacy("No longer applicable") == "expired"
    assert map_status_legacy("Unknown") == "active"


def test_normalize_doc_number():
    assert normalize_doc_number("  115/nq-hđbcqg  ") == "115/NQ-HĐBCQG"
    assert normalize_doc_number("01 / 2020 / TT") == "01 / 2020 / TT"
    assert normalize_doc_number(None) == ""


def test_parse_date_vi():
    assert parse_date_vi("15/03/2020") == date(2020, 3, 15)
    assert parse_date_vi("2020-03-15") == date(2020, 3, 15)
    assert parse_date_vi("garbage") is None
    assert parse_date_vi("") is None
    assert parse_date_vi("31/02/2020") is None


def test_count_articles():
    from scripts._law_ingest import count_articles
    assert count_articles("Điều 1. X\nĐIỀU 2. Y\nĐiều 3. Z") == 3
    assert count_articles("không có điều khoản số") == 0
    assert count_articles(None) == 0
