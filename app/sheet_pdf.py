"""Draw the same rows and columns used for an Excel download as a table PDF."""

from pathlib import Path

FONT_PATH = Path(__file__).resolve().parent / "static" / "fonts" / "Hind-Regular.ttf"


def table_pdf(headers: list[str], rows: list[list[str]], title: str, note: str = "") -> bytes:
    """Write one table. Column order and row order match the Excel sheet."""
    if not FONT_PATH.is_file():
        raise RuntimeError("The PDF font is not installed.")
    from fpdf import FPDF
    from fpdf.enums import TableBordersLayout, TableHeadingsDisplay
    from fpdf.fonts import FontFace

    headers = ["" if value is None else str(value) for value in headers]
    normalized = []
    for row in rows:
        values = ["" if value is None else str(value) for value in row]
        if len(values) < len(headers):
            values.extend([""] * (len(headers) - len(values)))
        normalized.append(values[: len(headers)])
    orientation = "L" if len(headers) > 7 else "P"

    class _Document(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Noto", size=8)
            self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")

    pdf = _Document(orientation=orientation, unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("Noto", "", str(FONT_PATH))
    pdf.set_font("Noto", size=9)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Noto", size=14)
    pdf.cell(0, 8, (title or "Records")[:120], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Noto", size=9)
    if note:
        pdf.multi_cell(0, 5, note)
        pdf.ln(1)
    heading = FontFace(fill_color=(232, 242, 241))
    with pdf.table(
        borders_layout=TableBordersLayout.ALL,
        headings_style=heading,
        repeat_headings=TableHeadingsDisplay.ON_TOP_OF_EVERY_PAGE,
        line_height=5.5,
        padding=1.2,
        text_align="LEFT",
        width=pdf.epw,
        col_widths=_relative_widths(headers, normalized),
    ) as table:
        head = table.row()
        for value in headers:
            head.cell(value or " ")
        for row in normalized:
            line = table.row()
            for value in row:
                line.cell(value or " ")
    return bytes(pdf.output())


def _relative_widths(headers: list[str], rows: list[list[str]]) -> tuple[float, ...]:
    if not headers:
        return ()
    from fpdf import FPDF

    probe = FPDF(unit="mm", format="A4")
    probe.add_font("Noto", "", str(FONT_PATH))
    probe.set_font("Noto", size=9)
    widths = []
    for index, header in enumerate(headers):
        sample = header or " "
        for row in rows[:20]:
            value = row[index]
            if len(value) > len(sample):
                sample = value[:40]
        widths.append(max(probe.get_string_width(sample) + 4, 16))
    return tuple(widths)
