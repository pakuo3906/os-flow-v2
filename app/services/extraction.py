from __future__ import annotations

import html
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PDF_LITERAL_RE = re.compile(r"\((?:\\.|[^()])*\)\s*Tj", re.S)
_PDF_ARRAY_RE = re.compile(r"\[(.*?)\]\s*TJ", re.S)
_PDF_STRING_RE = re.compile(r"\((?:\\.|[^()])*\)")
_IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def extract_text(filename: str, content: bytes, mime_type: str | None = None) -> str | None:
    extension = Path(filename).suffix.lower()
    mime_type = (mime_type or "").lower()

    if _is_text_like(extension, mime_type):
        return _decode_text(content)

    if extension in {".html", ".htm"} or "html" in mime_type:
        return _extract_html_text(content)

    if extension == ".json" or "json" in mime_type:
        return _extract_json_text(content)

    if extension == ".docx" or mime_type.endswith("wordprocessingml.document"):
        return _extract_docx_text(content)

    if _is_image(extension, mime_type):
        return _extract_image_text(content)

    if extension == ".pdf" or mime_type == "application/pdf":
        return _extract_pdf_text(content)

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
    cleaned = _HTML_TAG_RE.sub(" ", raw_text)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


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
        import pytesseract
    except Exception:
        return None

    try:
        with Image.open(BytesIO(content)) as image:
            if image.mode not in {"L", "RGB"}:
                image = image.convert("RGB")
            text = pytesseract.image_to_string(image)
    except Exception:
        return None

    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def _extract_pdf_text(content: bytes) -> str | None:
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

    cleaned = "\n".join(part.strip() for part in chunks if part.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
