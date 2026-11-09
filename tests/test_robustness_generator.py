"""Generator of the robustness set (M3.6): deterministic, traits really present, composition."""

from functools import cache

import pypdfium2 as pdfium
import pytest
import yaml

from faktur_beres.extraction.pdf_text import read_pdf_text
from faktur_beres.generator.label_schema import RobustnessLabel
from faktur_beres.generator.render_pdf import SYNTHETIC_MARK
from faktur_beres.generator.robust_build import assemble_robustness
from faktur_beres.generator.robust_catalog import ROBUST

A4 = (595.28, 841.89)
REQUIRED_TRAITS = {
    "pindaian_tanpa_teks",
    "multi_dokumen",
    "harga_termasuk_ppn",
    "ppn_11_tercetak",
    "stempel_dan_tanda_tangan",
    "tanpa_kode_produk",
    "pemisah_npwp_nsfp",
    "halaman_miring",
    "kertas_non_a4",
}


@cache
def files() -> dict[str, bytes]:
    return assemble_robustness()


def labels() -> dict[str, RobustnessLabel]:
    return {
        rel.removeprefix("skenario/").removesuffix(".yaml"): RobustnessLabel.model_validate(
            yaml.safe_load(content)
        )
        for rel, content in files().items()
        if rel.startswith("skenario/")
    }


def page_sizes(pdf: bytes) -> list[tuple[float, float]]:
    document = pdfium.PdfDocument(pdf)
    try:
        return [tuple(round(v, 2) for v in document[i].get_size()) for i in range(len(document))]
    finally:
        document.close()


def test_the_generator_is_deterministic() -> None:
    assert assemble_robustness() == files()


def test_composition_of_the_set() -> None:
    found = labels()
    assert 12 <= len(found) <= 14
    routes = [label.harapan.jalur_awal for label in found.values()]
    assert routes.count("A") >= 8 and len(routes) - routes.count("A") >= 2
    assert {label.kanal for label in found.values()} == {"unggahan"}
    assert sum(label.harapan.menunggu == "M4" for label in found.values()) <= 2
    traits = {trait for label in found.values() for trait in label.ciri}
    assert traits >= REQUIRED_TRAITS
    assert {
        "dua_halaman",
        "angka_gaya_inggris",
        "instruksi_di_gambar",
        "instruksi_jangan_disalin",
    } <= traits


def test_scanned_pdf_has_no_text_layer() -> None:
    for spec in ROBUST:
        if spec.scanned_pdf:
            layer = read_pdf_text(files()[f"berkas/{spec.id}/faktur.pdf"])
            assert not layer.has_text_layer and layer.char_count == 0


def test_non_a4_documents_are_not_a4() -> None:
    for spec in ROBUST:
        if "kertas_non_a4" in spec.traits:
            sizes = page_sizes(files()[f"berkas/{spec.id}/faktur.pdf"])
            assert all(abs(w - A4[0]) > 5 or abs(h - A4[1]) > 5 for w, h in sizes), spec.id
        elif spec.format == "pdf":
            assert set(page_sizes(files()[f"berkas/{spec.id}/faktur.pdf"])) == {A4}, spec.id


@pytest.mark.parametrize(
    "spec", [s for s in ROBUST if s.format == "pdf" and not s.scanned_pdf], ids=lambda s: s.id
)
def test_every_vector_page_carries_the_synthetic_mark(spec) -> None:  # type: ignore[no-untyped-def]
    pages = read_pdf_text(files()[f"berkas/{spec.id}/faktur.pdf"]).pages
    assert pages and all(SYNTHETIC_MARK in page for page in pages)


def test_multi_page_documents() -> None:
    by_id = {spec.id: spec for spec in ROBUST}
    assert len(page_sizes(files()["berkas/KTH-02/faktur.pdf"])) == 3
    two_pages = [s.id for s in by_id.values() if "dua_halaman" in s.traits]
    for document in two_pages:
        assert len(page_sizes(files()[f"berkas/{document}/faktur.pdf"])) == 2


def test_prices_including_ppn_add_up_to_the_total() -> None:
    for document, label in labels().items():
        truth = label.kebenaran_dokumen
        if "harga_termasuk_ppn" in label.ciri:
            assert sum(line.jumlah for line in truth.baris) == truth.total, document
            assert truth.subtotal + truth.ppn == truth.total
        else:
            assert sum(line.jumlah for line in truth.baris) == truth.subtotal, document
