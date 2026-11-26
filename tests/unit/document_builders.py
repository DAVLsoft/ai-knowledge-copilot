"""Shared helpers for generating valid/invalid PDF and DOCX test fixtures in memory."""

from io import BytesIO

from docx import Document as DocxDocument
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def build_pdf_bytes(pages: list[str], *, password: str | None = None) -> bytes:
    """Create an in-memory PDF with real extractable text streams per page."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            resources = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            page[NameObject("/Resources")] = resources
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
            page[NameObject("/Contents")] = writer._add_object(stream)

    if password is not None:
        writer.encrypt(password, algorithm="RC4-128")

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build_docx_bytes(
    paragraphs: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
) -> bytes:
    """Create an in-memory DOCX with optional paragraphs and a table."""
    doc = DocxDocument()
    for paragraph in paragraphs or []:
        doc.add_paragraph(paragraph)
    if table_rows:
        cols = max(len(row) for row in table_rows)
        table = doc.add_table(rows=len(table_rows), cols=cols)
        for row_idx, row in enumerate(table_rows):
            for col_idx, value in enumerate(row):
                table.rows[row_idx].cells[col_idx].text = value
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
