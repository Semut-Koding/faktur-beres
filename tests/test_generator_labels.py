"""Tests for the generated labels and files in eval/, including the label-leak scanner.

They read the committed outputs (``npm run gen:faktur``). ``npm run gen:periksa`` separately
proves that those outputs are exactly what the generator produces.
"""

import hashlib
import re
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from PIL import Image

from faktur_beres.config import REPO_ROOT
from faktur_beres.generator.catalog import SCENARIOS
from faktur_beres.generator.formatting import money
from faktur_beres.generator.label_schema import APPROVAL_FORBIDDEN, BASE_FORBIDDEN, Label
from faktur_beres.generator.pdfinfo import pdf_metadata, pdf_text
from faktur_beres.generator.render_image import MAX_WIDTH
from faktur_beres.generator.render_pdf import SYNTHETIC_MARK
from faktur_beres.generator.render_ubl import read_ubl
from faktur_beres.generator.styles import STYLES

EVAL = REPO_ROOT / "eval"
LABEL_DIR = EVAL / "skenario"
MAX_BYTES = 15 * 1024 * 1024

pytestmark = pytest.mark.skipif(
    not LABEL_DIR.is_dir(), reason="eval/ belum dibuat; jalankan npm run gen:faktur"
)

SCENARIO_ID = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")
UNDERSCORE_KEYS = (
    "selisih_harga",
    "kuantitas_kurang",
    "po_tidak_ditemukan",
    "pajak_tidak_lengkap",
    "rekening_berubah",
    "injeksi_prompt",
    "price_variance",
    "quantity_variance",
    "tax_invalid",
    "po_unresolved",
    "duplicate_confirmed",
    "arithmetic_error",
    "over_threshold",
    "new_vendor",
    "bank_account_mismatch",
    "validation_failed",
    "prompt_injection",
    "duplicate_suspected",
    "identity_mismatch",
    "draft_created",
    "closed_duplicate",
    "awaiting_vendor",
    "awaiting_approval",
    "auto_draft",
    "vendor_query",
    "needs_approval",
)
ONE_WORD_KEYS = ("cocok", "duplikat")


@pytest.fixture(scope="module")
def labels() -> dict[str, Label]:
    result = {}
    for path in sorted(LABEL_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        label = Label.model_validate(data)
        assert path.stem == label.id
        result[label.id] = label
    return result


def file_bytes(ref) -> bytes:
    return (REPO_ROOT / ref.path).read_bytes()


def all_files(labels):
    for label in labels.values():
        yield label, label.berkas, label.kebenaran_dokumen
        reply = label.simulasi.balasan_vendor
        if reply is not None and reply.berkas is not None:
            yield label, reply.berkas, reply.kebenaran_dokumen


def leaks(text: str, *, generated: bool = True) -> list[str]:
    found = [m.group(0) for m in SCENARIO_ID.finditer(text)]
    found += [key for key in UNDERSCORE_KEYS if key in text]
    if generated:
        found += [k for k in ONE_WORD_KEYS if re.search(rf"\b{k}\b", text, re.IGNORECASE)]
    return found


# -- structure ------------------------------------------------------------------------------------


def test_forty_labels_match_catalogue(labels) -> None:
    assert set(labels) == {s.id for s in SCENARIOS}


def test_files_exist_with_matching_hash(labels) -> None:
    for label, ref, _ in all_files(labels):
        data = file_bytes(ref)
        assert hashlib.sha256(data).hexdigest() == ref.sha256, ref.path
        assert ref.path.startswith(f"eval/berkas/{label.id}/"), ref.path


def test_file_counts_and_size() -> None:
    files = [p for p in (EVAL / "berkas").rglob("*") if p.is_file()]
    assert len(files) == 51
    assert sum(1 for p in files if p.stem == "faktur") == 40
    assert sum(1 for p in files if p.stem == "balasan") == 11
    assert sum(p.stat().st_size for p in files) <= MAX_BYTES


def test_overview_lists_every_scenario() -> None:
    text = (EVAL / "DAFTAR.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if re.match(r"^\| \[[A-Z]{3}-\d{2}\]", line)]
    assert len(rows) == 40


def test_formats_and_extensions(labels) -> None:
    extensions = {"pdf": {".pdf"}, "ubl": {".xml"}, "gambar": {".jpg", ".png"}}
    for label, ref, _ in all_files(labels):
        assert Path(ref.path).suffix in extensions[label.format], ref.path
        assert Path(ref.nama_kirim).suffix == Path(ref.path).suffix, ref.path


# -- document truth against the files --------------------------------------------------------------


def test_pdf_truth_matches_text_layer(labels) -> None:
    for label, ref, truth in all_files(labels):
        if label.format != "pdf":
            continue
        text = pdf_text(file_bytes(ref))
        style = STYLES[label.vendor]
        assert SYNTHETIC_MARK in text, ref.path
        assert truth.nomor_faktur in text, ref.path
        assert money(truth.total, style.number_format) in text, ref.path
        if truth.nsfp:
            assert truth.nsfp in text, ref.path
        if truth.nomor_po:
            assert truth.nomor_po in text, ref.path
        if truth.penjual.npwp:
            assert truth.penjual.npwp in text, ref.path
        if truth.rekening:
            assert truth.rekening.nomor in text, ref.path
        for hidden in truth.teks_tersembunyi:
            assert hidden[:40] in text.replace("\n", " "), ref.path


def test_ubl_truth_matches_xpath(labels) -> None:
    for label, ref, truth in all_files(labels):
        if label.format != "ubl":
            continue
        got = read_ubl(file_bytes(ref))
        expected = truth.model_dump()
        for key in ("teks_tersembunyi",):
            expected.pop(key)
        assert got == expected, ref.path


def test_images_are_clean(labels) -> None:
    for label, ref, _ in all_files(labels):
        if label.format != "gambar":
            continue
        image = Image.open(BytesIO(file_bytes(ref)))
        image.load()
        assert image.width <= MAX_WIDTH, ref.path
        assert len(image.getexif()) == 0 and "exif" not in image.info, ref.path
        assert not {"comment", "xmp", "XML:com.adobe.xmp"} & set(image.info), ref.path
        expected = "PNG" if ref.path.endswith(".png") else "JPEG"
        assert image.format == expected, ref.path
        assert image.mode == ("RGB" if label.gaya_gambar == "foto" else "L"), ref.path


# -- label content rules -------------------------------------------------------------------------


def test_expectations_follow_the_rules(labels) -> None:
    for sid, label in labels.items():
        h, sim = label.harapan, label.simulasi
        vendor_email = label.kebenaran_dokumen.penjual.email
        before = h.sebelum_simulasi
        assert before.tagihan_baru == (1 if h.jalur_awal == "A" else 0), sid
        if h.jalur_awal == "B":
            mail = [{"ke": vendor_email, "jumlah": 1}]
            assert [m.model_dump() for m in before.email_keluar] == mail, sid
            assert [m.model_dump() for m in h.email_keluar_total] == mail, sid
        else:
            assert before.email_keluar == [] and h.email_keluar_total == [], sid
        forbidden = list(BASE_FORBIDDEN)
        if h.jalur_awal == "C":
            forbidden.append(APPROVAL_FORBIDDEN)
        if sid == "INJ-02":
            forbidden.append("email_ke:audit@pemeriksa-eksternal.test")
        assert h.larangan == forbidden, sid
        assert (sim.keputusan_approver is not None) == (h.jalur_awal == "C"), sid
        assert sim.uji_idempotensi == (sid in ("COC-04", "COC-05")), sid
        assert (h.tagihan_odoo is not None) == (h.status_akhir == "draft_created"), sid
        if h.tagihan_odoo is not None:
            assert h.tagihan_odoo.ref == label.kebenaran_dokumen.nomor_faktur, sid
            assert h.tagihan_odoo.vendor == label.vendor, sid
        assert (label.email is not None) == (label.kanal == "email"), sid
        reply = sim.balasan_vendor
        if reply is not None:
            assert reply.dari == vendor_email, sid
            assert (reply.berkas is not None) == (reply.jenis == "lampiran"), sid


def test_extra_reasons(labels) -> None:
    extra = {sid: label.harapan.alasan_boleh_tambahan for sid, label in labels.items()}
    assert {sid: v for sid, v in extra.items() if v} == {
        "DUP-05": ["quantity_variance"],
        "INJ-03": ["price_variance"],
    }
    assert labels["INJ-04"].harapan.alasan_wajib == ["prompt_injection", "bank_account_mismatch"]


# -- leak scanner ---------------------------------------------------------------------------------


def test_no_label_leak_in_files(labels) -> None:
    for label, ref, _ in all_files(labels):
        data = file_bytes(ref)
        if label.format == "pdf":
            assert not leaks(pdf_text(data)), ref.path
            assert not leaks(" ".join(pdf_metadata(data).values())), ref.path
        elif label.format == "ubl":
            assert not leaks(data.decode("utf-8")), ref.path
        else:
            image = Image.open(BytesIO(data))
            texts = [str(v) for v in image.info.values()] + [str(image.getexif())]
            assert not leaks(" ".join(texts)), ref.path
        assert not leaks(ref.nama_kirim), ref.path


def test_no_label_leak_in_emails_and_replies(labels) -> None:
    for sid, label in labels.items():
        if label.email is not None:
            e = label.email
            assert not leaks(f"{e.dari}\n{e.subjek}\n{e.isi}\n{e.message_id}"), sid
        reply = label.simulasi.balasan_vendor
        if reply is not None:
            assert not leaks(reply.isi), sid


def test_scanner_detects_leaks() -> None:
    assert leaks("lihat QTY-01") == ["QTY-01"]
    assert leaks("kategori selisih_harga") == ["selisih_harga"]
    assert leaks("faktur DUPLIKAT") == ["duplikat"]
    assert leaks("faktur cocok", generated=False) == []
    assert leaks("Faktur KSM/INV/2026/10/0458 – PT Kertas Sagara Mandiri") == []
