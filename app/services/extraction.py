from __future__ import annotations

import html
import csv
import email
import email.policy
import json
import re
import tempfile
import zipfile
from contextlib import suppress
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PDF_LITERAL_RE = re.compile(r"\((?:\\.|[^()])*\)\s*Tj", re.S)
_PDF_ARRAY_RE = re.compile(r"\[(.*?)\]\s*TJ", re.S)
_PDF_STRING_RE = re.compile(r"\((?:\\.|[^()])*\)")
_IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class ExtractionDetails:
    text: str
    source_type: str
    engine: str


def get_extraction_capabilities() -> dict[str, bool]:
    pypdf_available = _has_module("pypdf")
    pdfplumber_available = _has_module("pdfplumber")
    pdf2image_available = _has_module("pdf2image")
    pillow_available = _has_module("PIL")
    pytesseract_available = _has_module("pytesseract")
    xlrd_available = _has_module("xlrd")
    extract_msg_available = _has_module("extract_msg")
    return {
        "pypdf": pypdf_available,
        "pdfplumber": pdfplumber_available,
        "pdf2image": pdf2image_available,
        "pillow": pillow_available,
        "pytesseract": pytesseract_available,
        "xlrd": xlrd_available,
        "extract_msg": extract_msg_available,
        "pdf_text_parsing_ready": pypdf_available or pdfplumber_available,
        "image_ocr_ready": pillow_available and pytesseract_available,
        "scanned_pdf_ocr_ready": pdf2image_available and pillow_available and pytesseract_available,
        "legacy_xls_ready": xlrd_available,
        "legacy_outlook_msg_ready": extract_msg_available,
}


def extract_text(filename: str, content: bytes, mime_type: str | None = None) -> str | None:
    details = extract_text_details(filename, content, mime_type)
    return None if details is None else details.text


def extract_text_details(filename: str, content: bytes, mime_type: str | None = None) -> ExtractionDetails | None:
    extension = Path(filename).suffix.lower()
    mime_type = (mime_type or "").lower()

    if extension == ".rtf" or "rtf" in mime_type:
        text = _extract_rtf_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="rtf", engine="builtin")

    if extension == ".ods" or "opendocument.spreadsheet" in mime_type:
        text = _extract_ods_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="spreadsheet", engine="builtin")

    if extension == ".odt" or "opendocument.text" in mime_type:
        text = _extract_odt_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="odt", engine="builtin")

    if extension == ".xls" or mime_type in {"application/vnd.ms-excel", "application/msexcel"}:
        text = _extract_xls_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="spreadsheet", engine="xlrd")

    if extension in {".xlsx", ".xlsm"} or "sheet" in mime_type or "excel" in mime_type:
        text = _extract_xlsx_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="spreadsheet", engine="builtin")

    if extension in {".xml", ".xhtml"} or mime_type in {"application/xml", "text/xml"} or mime_type.endswith("+xml"):
        text = _extract_xml_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="xml", engine="builtin")

    if extension == ".eml" or "message/rfc822" in mime_type or "email" in mime_type:
        text = _extract_eml_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="eml", engine="builtin")

    if extension == ".msg" or "vnd.ms-outlook" in mime_type or "ms-outlook" in mime_type:
        text = _extract_msg_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="msg", engine="extract_msg")

    if extension in {".csv", ".tsv"} or "csv" in mime_type or "tsv" in mime_type:
        delimiter = "\t" if extension == ".tsv" or "tsv" in mime_type else ","
        text = _extract_delimited_text(content, delimiter=delimiter)
        source_type = "tsv" if delimiter == "\t" else "csv"
        return None if text is None else ExtractionDetails(text=text, source_type=source_type, engine="builtin")

    if extension in {".html", ".htm"} or "html" in mime_type:
        text = _extract_html_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="html", engine="builtin")

    if _is_text_like(extension, mime_type):
        text = _decode_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="text", engine="builtin")

    if extension == ".json" or "json" in mime_type:
        text = _extract_json_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="json", engine="builtin")

    if extension == ".docx" or mime_type.endswith("wordprocessingml.document"):
        text = _extract_docx_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="docx", engine="builtin")

    if _is_image(extension, mime_type):
        text = _extract_image_text(content)
        return None if text is None else ExtractionDetails(text=text, source_type="image", engine="pytesseract")

    if extension == ".pdf" or mime_type == "application/pdf":
        return _extract_pdf_text_details(content)

    return None


def _is_text_like(extension: str, mime_type: str) -> bool:
    return extension in {".txt", ".md", ".csv", ".log", ".tsv", ".yaml", ".yml"} or mime_type.startswith("text/")


def _is_image(extension: str, mime_type: str) -> bool:
    return extension in _IMAGE_EXTENSIONS or mime_type.startswith("image/")


def _decode_text(content: bytes) -> str | None:
    for encoding in ("utf-8", "utf-8-sig", "cp932", "latin-1"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        text = text.strip()
        return text or None
    return None


def _extract_html_text(content: bytes) -> str | None:
    raw_text = _decode_text(content)
    if raw_text is None:
        return None
    raw_text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_text)
    cleaned = _HTML_TAG_RE.sub(" ", raw_text)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _extract_rtf_text(content: bytes) -> str | None:
    raw_text = _decode_text(content)
    if raw_text is None:
        return None
    cleaned = raw_text
    cleaned = re.sub(r"\\'[0-9a-fA-F]{2}", " ", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\d* ?", " ", cleaned)
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    cleaned = cleaned.replace(r"\par", "\n")
    cleaned = cleaned.replace(r"\tab", "\t")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _extract_xml_text(content: bytes) -> str | None:
    raw_text = _decode_text(content)
    if raw_text is None:
        return None
    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError:
        stripped = re.sub(r"<[^>]+>", " ", raw_text)
        stripped = html.unescape(stripped)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return stripped or None
    parts = [part.strip() for part in root.itertext() if part and part.strip()]
    cleaned = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return cleaned or None


def _extract_xlsx_text(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            shared_strings = _load_xlsx_shared_strings(archive)
            sheet_names = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
            chunks: list[str] = []
            for sheet_name in sorted(sheet_names):
                try:
                    sheet_xml = archive.read(sheet_name)
                except Exception:
                    continue
                sheet_text = _extract_xlsx_sheet_text(sheet_xml, shared_strings)
                if sheet_text:
                    chunks.append(sheet_text)
    except Exception:
        return None

    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _extract_ods_text(content: bytes) -> str | None:
    return _extract_opendocument_text(content)


def _extract_odt_text(content: bytes) -> str | None:
    return _extract_opendocument_text(content)


def _extract_opendocument_text(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml_bytes = archive.read("content.xml")
    except Exception:
        return None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    namespace = {
        "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    }
    text_namespace = namespace["text"]
    rows: list[str] = []
    for node in root.iter():
        tag = getattr(node, "tag", "")
        if not isinstance(tag, str):
            continue
        if not (tag == f"{{{text_namespace}}}p" or tag == f"{{{text_namespace}}}h"):
            continue
        node_text = "".join(part.strip() for part in node.itertext() if part and part.strip())
        if node_text:
            rows.append(node_text)
    cleaned = _normalize_extracted_text("\n".join(rows), preserve_newlines=True)
    return cleaned or None


def _extract_xls_text(content: bytes) -> str | None:
    try:
        import xlrd
    except Exception:
        return None

    try:
        workbook = xlrd.open_workbook(file_contents=content)
    except Exception:
        return None

    chunks: list[str] = []
    try:
        sheets = workbook.sheets()
    except Exception:
        return None

    for sheet in sheets:
        try:
            row_count = sheet.nrows
            col_count = sheet.ncols
        except Exception:
            continue
        for row_index in range(row_count):
            cells: list[str] = []
            for col_index in range(col_count):
                try:
                    value = sheet.cell_value(row_index, col_index)
                except Exception:
                    continue
                if value is None:
                    continue
                cell_text = str(value).strip()
                if cell_text:
                    cells.append(cell_text)
            if cells:
                chunks.append(" ".join(cells))

    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _load_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw_xml = archive.read("xl/sharedStrings.xml")
    except Exception:
        return []

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return []

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared_strings: list[str] = []
    for item in root.findall(f".//{namespace}si"):
        text_parts = [node.text or "" for node in item.findall(f".//{namespace}t")]
        shared_strings.append("".join(text_parts))
    return shared_strings


def _extract_xlsx_sheet_text(sheet_xml: bytes, shared_strings: list[str]) -> str | None:
    try:
        root = ET.fromstring(sheet_xml)
    except ET.ParseError:
        return None

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[str] = []
    for row in root.findall(f".//{namespace}row"):
        cells: list[str] = []
        for cell in row.findall(f"{namespace}c"):
            cell_type = cell.attrib.get("t")
            value_node = cell.find(f"{namespace}v")
            inline_string_node = cell.find(f"{namespace}is")
            cell_text = ""
            if cell_type == "s" and value_node is not None and value_node.text is not None:
                try:
                    shared_index = int(value_node.text)
                    cell_text = shared_strings[shared_index] if 0 <= shared_index < len(shared_strings) else value_node.text
                except Exception:
                    cell_text = value_node.text
            elif cell_type == "inlineStr" and inline_string_node is not None:
                text_parts = [node.text or "" for node in inline_string_node.findall(f".//{namespace}t")]
                cell_text = "".join(text_parts)
            elif value_node is not None and value_node.text is not None:
                cell_text = value_node.text
            if cell_text:
                cells.append(cell_text.strip())
        row_text = "\t".join(cell for cell in cells if cell)
        if row_text:
            rows.append(row_text)
    cleaned = _normalize_extracted_text("\n".join(rows), preserve_newlines=True)
    return cleaned or None


def _extract_eml_text(content: bytes) -> str | None:
    try:
        message = email.message_from_bytes(content, policy=email.policy.default)
    except Exception:
        return None

    chunks: list[str] = []
    subject = str(message.get("subject") or "").strip()
    if subject:
        chunks.append(f"Subject: {subject}")
    header_fields = ("from", "to", "cc", "date")
    for header_name in header_fields:
        header_value = str(message.get(header_name) or "").strip()
        if header_value:
            chunks.append(f"{header_name.title()}: {header_value}")
    for part in message.walk():
        content_type = (part.get_content_type() or "").lower()
        if part.is_multipart():
            continue
        if content_type == "text/plain":
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    payload = _decode_text(payload)
            if isinstance(payload, str) and payload.strip():
                chunks.append(payload)
        elif content_type == "text/html" and not chunks:
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    payload = _decode_text(payload)
            if isinstance(payload, str) and payload.strip():
                chunks.append(_extract_html_payload_text(payload))

    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _extract_msg_text(content: bytes) -> str | None:
    try:
        import extract_msg
    except Exception:
        return None

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".msg") as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        try:
            message = extract_msg.Message(temp_path)
        except Exception:
            return None

        chunks: list[str] = []
        subject = str(getattr(message, "subject", "") or "").strip()
        if subject:
            chunks.append(f"Subject: {subject}")
        for header_name in ("sender", "to", "cc", "date"):
            header_value = str(getattr(message, header_name, "") or "").strip()
            if header_value:
                chunks.append(f"{header_name.title()}: {header_value}")
        body = str(getattr(message, "body", "") or "").strip()
        if not body:
            body = str(getattr(message, "htmlBody", "") or "").strip()
            if body:
                body = _extract_html_payload_text(body)
        if body:
            chunks.append(body)

        cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
        return cleaned or None
    finally:
        with suppress(Exception):
            if "message" in locals() and hasattr(message, "close"):
                message.close()
        if temp_path is not None:
            with suppress(Exception):
                Path(temp_path).unlink(missing_ok=True)


def _extract_delimited_text(content: bytes, *, delimiter: str) -> str | None:
    raw_text = _decode_text(content)
    if raw_text is None:
        return None

    try:
        rows = list(csv.reader(raw_text.splitlines(), delimiter=delimiter))
    except Exception:
        return raw_text

    chunks: list[str] = []
    for row in rows:
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        if cells:
            chunks.append("\t".join(cells))
    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _extract_html_payload_text(value: str) -> str:
    extracted = _extract_html_text(value.encode("utf-8"))
    return extracted or value


def _extract_json_text(content: bytes) -> str | None:
    raw_text = _decode_text(content)
    if raw_text is None:
        return None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    pretty = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
    return pretty.strip() or None


def _extract_docx_text(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            with archive.open("word/document.xml") as document_file:
                xml_bytes = document_file.read()
    except Exception:
        return None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.findall(f".//{namespace}p"):
        text_parts = [node.text or "" for node in paragraph.findall(f".//{namespace}t")]
        paragraph_text = "".join(text_parts).strip()
        if paragraph_text:
            paragraphs.append(paragraph_text)
    extracted = "\n".join(paragraphs).strip()
    return extracted or None


def _extract_image_text(content: bytes) -> str | None:
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        with Image.open(BytesIO(content)) as image:
            text = _extract_text_from_image_object(image)
    except Exception:
        return None

    return text


def _extract_text_from_image_object(image: object) -> str | None:
    try:
        import pytesseract
    except Exception:
        return None

    try:
        prepared_image = _prepare_image_for_ocr(image)
        text = pytesseract.image_to_string(prepared_image)
    except Exception:
        return None

    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def _prepare_image_for_ocr(image: object) -> object:
    image = _apply_image_orientation(image)
    mode = getattr(image, "mode", None)
    if mode not in {"L", "RGB"} and hasattr(image, "convert"):
        try:
            image = image.convert("RGB")
        except Exception:
            return image

    if hasattr(image, "convert"):
        try:
            grayscale_image = image.convert("L")
        except Exception:
            return image
        return _apply_image_contrast(grayscale_image)

    return image


def _apply_image_orientation(image: object) -> object:
    try:
        from PIL import ImageOps
    except Exception:
        return image

    if hasattr(ImageOps, "exif_transpose"):
        try:
            return ImageOps.exif_transpose(image)
        except Exception:
            return image
    return image


def _apply_image_contrast(image: object) -> object:
    try:
        from PIL import ImageOps
    except Exception:
        return image

    if hasattr(ImageOps, "autocontrast"):
        try:
            return ImageOps.autocontrast(image)
        except Exception:
            return image
    return image


def _extract_pdf_text(content: bytes) -> str | None:
    details = _extract_pdf_text_details(content)
    return None if details is None else details.text


def _extract_pdf_text_details(content: bytes) -> ExtractionDetails | None:
    extracted = _extract_pdf_text_with_pypdf(content)
    if extracted is not None:
        return ExtractionDetails(text=extracted, source_type="pdf", engine="pypdf")

    extracted = _extract_pdf_text_with_pdfplumber(content)
    if extracted is not None:
        return ExtractionDetails(text=extracted, source_type="pdf", engine="pdfplumber")

    extracted = _extract_pdf_text_with_ocr(content)
    if extracted is not None:
        return ExtractionDetails(text=extracted, source_type="pdf", engine="pdf2image+pytesseract")

    extracted = _extract_pdf_text_with_regex(content)
    if extracted is not None:
        return ExtractionDetails(text=extracted, source_type="pdf", engine="regex")
    return None


def _extract_pdf_text_with_pypdf(content: bytes) -> str | None:
    try:
        from pypdf import PdfReader
    except Exception:
        return None

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return None

    chunks: list[str] = []
    for page in getattr(reader, "pages", []):
        try:
            page_text = page.extract_text()
        except Exception:
            continue
        if page_text:
            chunks.append(page_text)

    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _extract_pdf_text_with_pdfplumber(content: bytes) -> str | None:
    try:
        import pdfplumber
    except Exception:
        return None

    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            chunks = []
            for page in pdf.pages:
                try:
                    page_text = page.extract_text()
                except Exception:
                    continue
                if page_text:
                    chunks.append(page_text)
    except Exception:
        return None

    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _extract_pdf_text_with_ocr(content: bytes) -> str | None:
    try:
        from pdf2image import convert_from_bytes
    except Exception:
        return None

    try:
        pages = convert_from_bytes(content, dpi=300)
    except Exception:
        return None

    chunks: list[str] = []
    for page in pages:
        page_text = _extract_text_from_image_object(page)
        if page_text:
            chunks.append(page_text)

    cleaned = _normalize_extracted_text("\n".join(chunks), preserve_newlines=True)
    return cleaned or None


def _extract_pdf_text_with_regex(content: bytes) -> str | None:
    try:
        text = content.decode("latin-1", errors="ignore")
    except Exception:
        return None

    chunks: list[str] = []
    for match in _PDF_LITERAL_RE.findall(text):
        chunks.append(_unescape_pdf_string(match[1 : match.rfind(")")]))
    for array_match in _PDF_ARRAY_RE.findall(text):
        for string_match in _PDF_STRING_RE.findall(array_match):
            chunks.append(_unescape_pdf_string(string_match[1:-1]))

    cleaned = _normalize_extracted_text("\n".join(part.strip() for part in chunks if part.strip()))
    return cleaned or None


def _unescape_pdf_string(value: str) -> str:
    value = value.replace(r"\n", "\n")
    value = value.replace(r"\r", "\r")
    value = value.replace(r"\t", "\t")
    value = value.replace(r"\b", "\b")
    value = value.replace(r"\f", "\f")
    value = value.replace(r"\(", "(")
    value = value.replace(r"\)", ")")
    value = value.replace(r"\\", "\\")
    return value


def _normalize_extracted_text(value: str, preserve_newlines: bool = False) -> str:
    if preserve_newlines:
        lines = [re.sub(r"\s+", " ", line).strip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        cleaned_lines = [line for line in lines if line]
        return "\n".join(cleaned_lines).strip()
    return re.sub(r"\s+", " ", value).strip()


def _has_module(module_name: str) -> bool:
    try:
        __import__(module_name)
    except Exception:
        return False
    return True
