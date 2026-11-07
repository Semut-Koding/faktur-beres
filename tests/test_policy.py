"""Tests for the policy configuration (data/kebijakan/parameter.yaml, SPEC §15.5, BR-28)."""

import re
from decimal import Decimal
from fractions import Fraction

import pytest
import yaml
from pydantic import ValidationError

from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset import load_dataset
from faktur_beres.domain.policy import POLICY_PATH, PolicyConfig, load_policy

SCENARIO_ID = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")


def spec_policy_rows() -> dict[str, str]:
    """``Parameter -> Nilai bawaan`` from the table in SPEC §15.5."""
    text = (REPO_ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    section = text.split("### 15.5 Kebijakan bawaan", 1)[1].split("### 15.6", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Parameter", "---"):
            rows[cells[0]] = cells[1]
    return rows


def rupiah(text: str) -> list[Decimal]:
    return [Decimal(m.replace(".", "")) for m in re.findall(r"Rp([\d.]+)", text)]


@pytest.fixture(scope="module")
def policy() -> PolicyConfig:
    return load_policy()


@pytest.mark.aturan("BR-28")
def test_values_match_spec_15_5(policy: PolicyConfig) -> None:
    spec = spec_policy_rows()
    assert rupiah(spec["Ambang persetujuan"]) == [policy.approval_threshold]
    assert "total termasuk PPN" in spec["Ambang persetujuan"]
    assert re.match(r"\+(\d+)%", spec["Toleransi harga"]).group(1) == str(
        policy.price_tolerance_percent
    )
    assert Decimal(spec["Toleransi kuantitas"]) == policy.quantity_tolerance
    r = policy.rounding
    assert rupiah(spec["Toleransi pembulatan"]) == [r.per_line, r.tax, r.total]
    rate, factor = re.fullmatch(r"(\d+)% dan (\d+/\d+)", spec["Tarif PPN dan faktor DPP"]).groups()
    assert Decimal(rate) == policy.tax.vat_rate_percent
    assert factor == str(policy.tax.dpp_factor)
    code = re.match(r"`(\d{2})`", spec["Kode transaksi yang diharapkan"]).group(1)
    assert code == policy.tax.expected_transaction_code
    assert re.match(r"(\d+) kali", spec["Batas perbaikan ekstraksi"]).group(1) == str(
        policy.max_extraction_repairs
    )
    assert spec["Definisi vendor baru"] == "Tanpa tagihan vendor terposting"
    assert policy.new_vendor_definition == "tanpa_tagihan_terposting"
    days, reminders = re.fullmatch(
        r"(\d+) hari kerja, (\d+) pengingat", spec["Tunggu balasan vendor"]
    ).groups()
    assert (int(days), int(reminders)) == (
        policy.vendor_reply.wait_working_days,
        policy.vendor_reply.reminders,
    )
    assert spec["Domain email internal"] == f"`{policy.internal_email_domain}`"


@pytest.mark.aturan("BR-28")
def test_threshold_matches_dataset(policy: PolicyConfig) -> None:
    assert policy.approval_threshold == load_dataset().approval_threshold


def test_effective_rate_is_eleven_percent(policy: PolicyConfig) -> None:
    assert policy.tax.effective_rate == Fraction(11, 100)
    assert policy.tax.dpp_factor.fraction == Fraction(11, 12)


def test_policy_file_has_no_scenario_ids() -> None:
    assert not SCENARIO_ID.search(POLICY_PATH.read_text(encoding="utf-8"))


def raw_policy() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ambang_persetujuan_rupiah",), -1),
        (("toleransi_harga_persen",), -1),
        (("toleransi_kuantitas",), -1),
        (("toleransi_pembulatan", "ppn_rupiah"), -1),
        (("batas_perbaikan_ekstraksi",), -1),
        (("balasan_vendor", "jumlah_pengingat"), -1),
        (("toleransi_harga_persen",), 1.5),
        (("pajak", "faktor_dpp_nilai_lain"), "12/11"),
        (("pajak", "faktor_dpp_nilai_lain"), "0/12"),
        (("pajak", "faktor_dpp_nilai_lain"), "11/0"),
        (("pajak", "faktor_dpp_nilai_lain"), "sebelas per dua belas"),
        (("pajak", "kode_transaksi_diharapkan"), "4"),
        (("pajak", "tarif_ppn_persen"), 0),
        (("vendor_baru",), "lain"),
        (("domain_email_internal",), "Arunika Test"),
    ],
)
def test_invalid_values_are_rejected(path: tuple[str, ...], value: object) -> None:
    raw = raw_policy()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(raw)


def test_unknown_keys_are_rejected() -> None:
    raw = raw_policy()
    raw["toleransi_lain"] = 3
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(raw)


def test_decimal_strings_are_accepted() -> None:
    raw = raw_policy()
    raw["toleransi_harga_persen"] = "0.5"
    assert PolicyConfig.model_validate(raw).price_tolerance_percent == Decimal("0.5")
