"""Generic multi-page PDF table renderer, shared by both report types."""

from __future__ import annotations

from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_styles = getSampleStyleSheet()

TITLE_STYLE = ParagraphStyle("ReportTitle", parent=_styles["Title"], fontSize=16, spaceAfter=4)
SUBTITLE_STYLE = ParagraphStyle("ReportSubtitle", parent=_styles["Normal"], fontSize=9, textColor=colors.grey)
CELL_STYLE = ParagraphStyle("Cell", parent=_styles["Normal"], fontSize=8, leading=10)


def render_table_pdf(
    output_path: str,
    title: str,
    subtitle_lines: list[str],
    headers: list[str],
    rows: list[list],
    col_widths: list[float] | None = None,
    landscape_page: bool = False,
):
    """
    Renders a titled, multi-page table PDF. `rows` values are stringified;
    wrap long text values in reportlab Paragraphs beforehand if you need
    wrapping (the first column already is, see build_row).
    """
    pagesize = landscape(A4) if landscape_page else A4

    doc = SimpleDocTemplate(
        output_path,
        pagesize=pagesize,
        topMargin=18 * mm,
        bottomMargin=14 * mm,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
    )

    story = [Paragraph(title, TITLE_STYLE)]

    for line in subtitle_lines:
        story.append(Paragraph(line, SUBTITLE_STYLE))

    story.append(Spacer(1, 10))

    table_data = [headers] + rows

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5233")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    story.append(table)

    doc.build(story)


def cell(text) -> Paragraph:
    """Wraps a value in a Paragraph so long text wraps within its column."""
    return Paragraph(str(text), CELL_STYLE)


def now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")
