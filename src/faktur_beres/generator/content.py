"""Content signatures of generated files, for ``gen:periksa`` (M3.1).

Compressed outputs (PDF streams, PNG) can differ byte for byte between machines because zlib
builds compress differently, while what they contain is the same. ``gen:periksa`` therefore compares
content:

* PDF: page count, page size, text of every page (pypdfium2), and the bitmap of every page
  rendered at 50 DPI;
* PNG and JPEG: mode, size, and decoded pixels;
* label YAML: the parsed data without the ``sha256`` values (they hash the compressed files);
* anything else: the bytes.
"""

import hashlib
from io import BytesIO
from typing import Any

import pypdfium2 as pdfium
import yaml
from PIL import Image

RENDER_DPI = 50


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_signature(data: bytes) -> tuple[Any, ...]:
    pdf = pdfium.PdfDocument(data)
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range().replace("\r\n", "\n")
            textpage.close()
            image = page.render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
            page.close()
            pages.append(
                (round(width, 2), round(height, 2), text, image.size, _sha(image.tobytes()))
            )
        return tuple(pages)
    finally:
        pdf.close()


def image_signature(data: bytes) -> tuple[Any, ...]:
    image = Image.open(BytesIO(data))
    image.load()
    return (image.mode, image.size, _sha(image.tobytes()))


def _without_hashes(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_hashes(v) for k, v in value.items() if k != "sha256"}
    if isinstance(value, list):
        return [_without_hashes(v) for v in value]
    return value


def label_signature(data: bytes) -> Any:
    return _without_hashes(yaml.safe_load(data.decode("utf-8")))


def content_signature(name: str, data: bytes) -> Any:
    """What must be equal for two files of this name to have the same content."""
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix == "pdf":
        return ("pdf", pdf_signature(data))
    if suffix in ("png", "jpg", "jpeg"):
        return ("image", image_signature(data))
    if suffix in ("yaml", "yml"):
        return ("label", label_signature(data))
    return ("bytes", _sha(data))


def file_refs(label: Any) -> list[tuple[str, str]]:
    """``(path, sha256)`` of every file a parsed label points to."""
    found: list[tuple[str, str]] = []
    if isinstance(label, dict):
        if isinstance(label.get("path"), str) and isinstance(label.get("sha256"), str):
            found.append((label["path"], label["sha256"]))
        for value in label.values():
            found += file_refs(value)
    elif isinstance(label, list):
        for value in label:
            found += file_refs(value)
    return found
