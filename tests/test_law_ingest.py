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
