"""The policy documents: same numbers as the configuration (BR-28), no labels, stable chunks."""

import re
from decimal import Decimal
from typing import Any, get_args

import pytest
import yaml

from faktur_beres.dataset import load_dataset
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.evals.labels import load_labels
from faktur_beres.generator.label_schema import Category
from faktur_beres.tools.policy_docs import DOCUMENTS, POLICY_DIR, chunks_of, load_chunks

PARAMETERS = yaml.safe_load((POLICY_DIR / "parameter.yaml").read_text(encoding="utf-8"))
TEXTS = {name: (POLICY_DIR / name).read_text(encoding="utf-8") for _, name in DOCUMENTS}
MARKED = re.compile(
    r"(Rp[\d.]+|[\d/.,]+)\s*%?(?:\s+(?:hari kerja|pengingat))?\s*<!-- param: ([\w.]+) -->"
)
POLICY_NUMBER = re.compile(r"Rp\s?\d[\d.]*|\d+\s*%|\d+\s+hari")
#: Ordinary Indonesian words that the outline requires; the other category keys are checked.
ORDINARY_WORDS = frozenset({"cocok", "duplikat"})


def parameter(path: str) -> Any:
    value: Any = PARAMETERS
    for key in path.split("."):
        value = value[key]
    return value


def unmarked(text: str) -> list[str]:
    return POLICY_NUMBER.findall(MARKED.sub("", text))


def _number(text: str) -> str:
    return text.removeprefix("Rp").replace(".", "")


@pytest.mark.aturan("BR-28")
@pytest.mark.parametrize("name", sorted(TEXTS))
def test_every_marked_number_equals_the_configuration(name: str) -> None:
    for printed, path in MARKED.findall(TEXTS[name]):
        expected = parameter(path)
        if isinstance(expected, str):
            assert printed == expected, path
        else:
            assert Decimal(_number(printed)) == Decimal(expected), path


@pytest.mark.aturan("BR-28")
def test_the_required_parameters_are_marked() -> None:
    marks = {path for text in TEXTS.values() for _, path in MARKED.findall(text)}
    assert marks >= {
        "ambang_persetujuan_rupiah",
        "toleransi_harga_persen",
        "toleransi_kuantitas",
        "toleransi_pembulatan.ppn_rupiah",
        "pajak.tarif_ppn_persen",
        "pajak.faktor_dpp_nilai_lain",
        "pajak.kode_transaksi_diharapkan",
        "balasan_vendor.tunggu_hari_kerja",
        "balasan_vendor.jumlah_pengingat",
    }


@pytest.mark.aturan("BR-28")
@pytest.mark.parametrize("name", sorted(TEXTS))
def test_no_rupiah_percent_or_day_number_without_a_marker(name: str) -> None:
    assert unmarked(TEXTS[name]) == []


def test_the_marker_check_would_notice_a_different_number() -> None:
    [(printed, path)] = MARKED.findall("harga 2%<!-- param: toleransi_harga_persen -->")
    assert Decimal(_number(printed)) != Decimal(parameter(path))
    assert unmarked("Rp100 dan 3% selama 2 hari kerja") == ["Rp100", "3%", "2 hari"]


def test_chunks_are_per_sub_article_and_carry_no_marker() -> None:
    chunks = load_chunks()
    refs = [c.ref for c in chunks]
    assert len(refs) == len(set(refs))
    assert {"SOP-8.2", "SOP-3.3", "SP-3"} <= set(refs)
    assert all("<!--" not in c.text for c in chunks)
    by_ref = {c.ref: c for c in chunks}
    assert "(a) masalah yang sama" in by_ref["SOP-8.2"].text
    assert by_ref["SOP-3.3"].title == "Pencocokan tiga arah"


def test_chunks_are_deterministic() -> None:
    assert load_chunks() == load_chunks()
    first, second = load_chunks()[0], load_chunks()[0]
    assert first.sha256 == second.sha256


def test_an_article_without_sub_articles_is_one_chunk() -> None:
    [chunk] = chunks_of("X", "d.md", "## 3. Judul\nBaris satu.\nBaris dua.\n")
    assert (chunk.ref, chunk.title, chunk.text) == ("X-3", "Judul", "Baris satu. Baris dua.")


def _forbidden() -> list[str]:
    ds = load_dataset()
    values = [v.name for v in ds.vendors] + [v.billing_email for v in ds.vendors]
    values += [v.npwp for v in ds.vendors] + [po.number for po in ds.purchase_orders]
    values += [code.value for code in ReasonCode]
    values += [category for category in get_args(Category) if category not in ORDINARY_WORDS]
    for scenario, label in load_labels().items():
        values += [scenario, label.varian]
    return values


@pytest.mark.parametrize("name", sorted(TEXTS))
def test_no_label_or_dataset_value_is_in_a_policy_document(name: str) -> None:
    text = TEXTS[name].casefold()
    assert [v for v in _forbidden() if v and v.casefold() in text] == []


@pytest.mark.parametrize("name", sorted(TEXTS))
def test_documents_are_marked_synthetic(name: str) -> None:
    assert "DOKUMEN SINTETIS – UNTUK PENGUJIAN" in TEXTS[name]
