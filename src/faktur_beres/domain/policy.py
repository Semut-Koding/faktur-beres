"""Policy configuration (SPEC §15.5) and its loader.

Thresholds and tolerances come from ``data/kebijakan/parameter.yaml`` and nowhere else (BR-28). The
model is passed to the rules as a parameter; nothing here is a module-level global setting.
Amounts are ``Decimal`` rupiah, percentages ``Decimal`` percent, and the DPP factor an exact ratio.
YAML floats are refused so that no binary fraction ever reaches a money computation.
"""

from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.base import DomainModel

POLICY_PATH = REPO_ROOT / "data" / "kebijakan" / "parameter.yaml"


class Ratio(DomainModel):
    """An exact fraction such as 11/12; written ``"11/12"`` in YAML."""

    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, value: Any) -> Any:
        if isinstance(value, str):
            parts = value.replace(" ", "").split("/")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                raise ValueError(f"pecahan tidak valid: {value!r} (contoh: 11/12)")
            return {"numerator": int(parts[0]), "denominator": int(parts[1])}
        return value

    @model_validator(mode="after")
    def _proper(self) -> "Ratio":
        if self.numerator > self.denominator:
            raise ValueError("faktor DPP tidak boleh lebih dari 1")
        return self

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"


class RoundingTolerance(DomainModel):
    per_line: Decimal = Field(alias="per_baris_rupiah", ge=0)
    tax: Decimal = Field(alias="ppn_rupiah", ge=0)
    total: Decimal = Field(alias="total_rupiah", ge=0)


class TaxPolicy(DomainModel):
    vat_rate_percent: Decimal = Field(alias="tarif_ppn_persen", gt=0, le=100)
    dpp_factor: Ratio = Field(alias="faktor_dpp_nilai_lain")
    expected_transaction_code: str = Field(alias="kode_transaksi_diharapkan", pattern=r"^\d{2}$")

    @property
    def vat_rate(self) -> Fraction:
        """The VAT rate as an exact fraction (12% -> 3/25)."""
        return Fraction(self.vat_rate_percent) / 100

    @property
    def effective_rate(self) -> Fraction:
        """PPN as a fraction of the selling price: rate x DPP factor (12% x 11/12 = 11%)."""
        return self.vat_rate * self.dpp_factor.fraction


class VendorReplyPolicy(DomainModel):
    wait_working_days: int = Field(alias="tunggu_hari_kerja", ge=0)
    reminders: int = Field(alias="jumlah_pengingat", ge=0)


class PolicyConfig(DomainModel):
    """All parameters of SPEC §15.5. Validated: nothing negative, the DPP factor a proper ratio."""

    version: int = Field(alias="versi", ge=1)
    approval_threshold: Decimal = Field(alias="ambang_persetujuan_rupiah", ge=0)
    price_tolerance_percent: Decimal = Field(alias="toleransi_harga_persen", ge=0)
    quantity_tolerance: Decimal = Field(alias="toleransi_kuantitas", ge=0)
    rounding: RoundingTolerance = Field(alias="toleransi_pembulatan")
    tax: TaxPolicy = Field(alias="pajak")
    max_extraction_repairs: int = Field(alias="batas_perbaikan_ekstraksi", ge=0)
    new_vendor_definition: Literal["tanpa_tagihan_terposting"] = Field(alias="vendor_baru")
    vendor_reply: VendorReplyPolicy = Field(alias="balasan_vendor")
    internal_email_domain: str = Field(
        alias="domain_email_internal", pattern=r"^[a-z0-9-]+(\.[a-z0-9-]+)+$"
    )


def load_policy(path: Path | str = POLICY_PATH) -> PolicyConfig:
    """Read and validate the policy file. Raises ``pydantic.ValidationError`` on bad values."""
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return PolicyConfig.model_validate(raw)
