import io

from pypdf import PdfReader

from app.sheet_pdf import table_pdf


def _text(payload: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(payload)).pages)


def test_pdf_uses_one_table_with_the_excel_columns():
    headers = ["Employee code", "Name", "Mobile number", "Facility"] + [f"Column {index:02d}" for index in range(8)]
    rows = [["EMP-1", "राहुल", "00712", "Latehar PHC"] + [f"value {index}" for index in range(8)]]
    rows.extend(
        [[f"EMP-{number}", "Person", "9867001001", "Latehar PHC"] + ["x"] * 8 for number in range(2, 42)]
    )
    payload = table_pdf(
        headers,
        rows,
        "Employees",
        "This PDF contains all filtered records, not only the current page.",
    )
    reader = PdfReader(io.BytesIO(payload))
    first = reader.pages[0].extract_text() or ""
    document = _text(payload)
    assert "Column group" not in document
    for header in headers:
        assert header in first
    assert "राहुल" in first
    assert "00712" in first
    assert len(reader.pages) >= 2
    assert "Employee code" in (reader.pages[1].extract_text() or "")
    assert float(reader.pages[0].mediabox.width) > float(reader.pages[0].mediabox.height)


def test_excel_to_pdf_page_is_not_available(admin):
    assert admin.get("/tools/excel-pdf").status_code == 404
