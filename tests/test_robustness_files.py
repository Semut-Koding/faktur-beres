"""The committed robustness set (M3.6): valid labels, files that match them, and no label leak.

Nothing an agent reads may reveal the evaluation: the document ID, a trait key, a reason code or a
word that names the expected answer never appears in a file's text, its metadata, or the name it is
uploaded under.
"""

import hashlib
from io import BytesIO

import pypdfium2 as pdfium
import pytest
from PIL import Image

from faktur_beres.config import REPO_ROOT
from faktur_beres.evals.labels import ROBUSTNESS_DIR, load_robustness_labels
from faktur_beres.extraction.pdf_text import read_pdf_text
from faktur_beres.generator.label_schema import RobustnessLabel

LABELS = load_robustness_labels()
ANSWER_WORDS = ("ketahanan", "jalur", "harapan", "menunggu", "alasan", "near-miss", "near miss")
REASONS = (
    "price_variance",
    "quantity_variance",
    "tax_invalid",
    "arithmetic_error",
    "prompt_injection",
    "validation_failed",
    "bank_account_mismatch",
)


def forbidden(label: RobustnessLabel) -> list[str]:
    return ["KTH", label.id, *label.ciri, *REASONS, *ANSWER_WORDS]


def readable_text(path_text: str, data: bytes) -> str:
    """Text an agent could read from the file: text layer and metadata (PDF), metadata (image).

    Raw compressed bytes are not searched: random image data holds any three letters by chance.
    """
    if path_text.endswith(".pdf"):
        parts = list(read_pdf_text(data).pages)
        document = pdfium.PdfDocument(data)
        try:
            parts += [str(v) for v in document.get_metadata_dict().values()]
        finally:
            document.close()
        return "\n".join(parts)
    image = Image.open(BytesIO(data))
    parts = [
        f"{key}={value}" for key, value in image.info.items() if isinstance(value, str | bytes)
    ]
    parts += [str(value) for value in image.getexif().values()]
    return "\n".join(parts)


def test_there_are_13_labels_with_their_files() -> None:
    assert sorted(LABELS) == [f"KTH-{n:02d}" for n in range(1, 14)]
    for label in LABELS.values():
        data = (REPO_ROOT / label.berkas.path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == label.berkas.sha256
        assert label.berkas.path.startswith("eval/ketahanan/berkas/")
    assert (ROBUSTNESS_DIR.parent / "DAFTAR.md").is_file()


@pytest.mark.parametrize("label", LABELS.values(), ids=lambda label: label.id)
def test_no_label_leaks_into_the_file(label: RobustnessLabel) -> None:
    text = readable_text(label.berkas.path, (REPO_ROOT / label.berkas.path).read_bytes())
    folded, name = text.casefold(), label.berkas.nama_kirim.casefold()
    for word in forbidden(label):
        assert word.casefold() not in folded, (label.id, word)
        assert word.casefold() not in name, (label.id, word)


def test_the_leak_check_reads_the_text_layer() -> None:
    label = LABELS["KTH-03"]
    text = readable_text(label.berkas.path, (REPO_ROOT / label.berkas.path).read_bytes())
    assert label.kebenaran_dokumen.nomor_faktur in text
