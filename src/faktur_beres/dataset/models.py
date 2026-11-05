"""Strict Pydantic models for ``data/seed/dataset.yaml``.

YAML keys are Indonesian; attribute names are English and mapped with aliases. Unknown keys are
rejected (``extra="forbid"``) so a typo in the dataset fails loudly instead of being ignored.
All money values are whole rupiah (``int``); nothing here uses ``float``.
"""

from datetime import date
from functools import cached_property
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.dataset.money import ppn_amount, total_with_ppn


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PurchaseTax(_Model):
    key: str = Field(alias="kunci")
    name: str = Field(alias="nama")
    effective_rate_percent: int = Field(alias="tarif_efektif_persen", gt=0, lt=100)
    note: str = Field(alias="keterangan")


class Company(_Model):
    name: str = Field(alias="nama")
    npwp: str
    street: str = Field(alias="alamat")
    city: str = Field(alias="kota")
    state: str = Field(alias="provinsi")
    zip: str = Field(alias="kode_pos")
    country: str = Field(alias="negara")
    currency: str = Field(alias="mata_uang")
    email_ap: str
    email_finance: str
    email_approver: str
    website: str = Field(alias="situs")


class Bank(_Model):
    key: str = Field(alias="kunci")
    name: str = Field(alias="nama")


class User(_Model):
    key: str = Field(alias="kunci")
    name: str = Field(alias="nama")
    login: str
    note: str = Field(alias="keterangan")


class BankAccount(_Model):
    bank: str
    number: str = Field(alias="nomor")
    holder: str = Field(alias="atas_nama")


InvoiceChannel = Literal["pdf", "ubl", "gambar"]


class Vendor(_Model):
    key: str = Field(alias="kunci")
    name: str = Field(alias="nama")
    npwp: str
    street: str = Field(alias="alamat")
    city: str = Field(alias="kota")
    state: str = Field(alias="provinsi")
    zip: str = Field(alias="kode_pos")
    billing_email: str = Field(alias="email_penagihan")
    bank_account: BankAccount = Field(alias="rekening")
    invoice_number_format: str = Field(alias="format_nomor_faktur")
    invoice_channels: tuple[InvoiceChannel, ...] = Field(alias="kanal_faktur")
    # Only used by dataset tests; the seed never writes it to Odoo (see dataset.yaml header).
    history_note: Literal["mapan", "baru"] = Field(alias="catatan_riwayat")
    payment_term_days: int = Field(alias="termin_hari", gt=0)


class ExternalVendor(_Model):
    """A vendor that is deliberately *not* registered in Odoo."""

    key: str = Field(alias="kunci")
    name: str = Field(alias="nama")
    npwp: str
    street: str = Field(alias="alamat")
    city: str = Field(alias="kota")
    state: str = Field(alias="provinsi")
    zip: str = Field(alias="kode_pos")
    billing_email: str = Field(alias="email_penagihan")
    bank_account: BankAccount = Field(alias="rekening")
    invoice_number_format: str = Field(alias="format_nomor_faktur")
    note: str = Field(alias="catatan")


class Product(_Model):
    key: str = Field(alias="kunci")
    code: str = Field(alias="kode")
    name: str = Field(alias="nama")
    uom: Literal["kg", "Units"] = Field(alias="satuan")
    purchase_price: int = Field(alias="harga_beli", gt=0)
    vendor: str
    vendor_code: str = Field(alias="kode_vendor")
    kind: Literal["barang"] = Field(alias="jenis")
    bill_control: Literal["kuantitas_diterima"] = Field(alias="kontrol_tagihan")


class POLine(_Model):
    product: str = Field(alias="produk")
    qty: int = Field(gt=0)
    unit_price: int = Field(alias="harga_satuan", gt=0)

    @property
    def subtotal(self) -> int:
        return self.qty * self.unit_price


class Receipt(_Model):
    received_on: date = Field(alias="tanggal")
    qty: dict[str, int]
    remainder: Literal["backorder"] | None = Field(default=None, alias="sisa")


class VendorBill(_Model):
    vendor_invoice_number: str = Field(alias="nomor_faktur_vendor")
    invoice_date: date = Field(alias="tanggal_faktur")
    status: Literal["diposting"]


POStatus = Literal["dikonfirmasi", "batal", "rfq"]


class PurchaseOrder(_Model):
    key: str = Field(alias="kunci")
    number: str = Field(alias="nomor")
    vendor: str
    order_date: date = Field(alias="tanggal_pesan")
    status: POStatus
    lines: tuple[POLine, ...] = Field(alias="baris", min_length=1)
    receipts: tuple[Receipt, ...] = Field(default=(), alias="penerimaan")
    bills: tuple[VendorBill, ...] = Field(default=(), alias="tagihan_vendor")
    planned_receipt_date: date | None = Field(default=None, alias="tanggal_rencana_terima")

    # -- amounts (whole rupiah) ----------------------------------------------------------------

    @property
    def subtotal(self) -> int:
        return sum(line.subtotal for line in self.lines)

    def ppn(self, rate_percent: int = 11) -> int:
        return ppn_amount(self.subtotal, rate_percent)

    def total(self, rate_percent: int = 11) -> int:
        return total_with_ppn(self.subtotal, rate_percent)

    # -- quantities -----------------------------------------------------------------------------

    def line_for(self, product_key: str) -> POLine:
        for line in self.lines:
            if line.product == product_key:
                return line
        raise KeyError(f"{self.number} tidak punya baris produk {product_key}")

    def received_qty(self, product_key: str) -> int:
        return sum(receipt.qty.get(product_key, 0) for receipt in self.receipts)

    def billed_qty(self, product_key: str) -> int:
        """Quantity billed by the history bills (a bill from a PO bills what was received)."""
        return self.received_qty(product_key) if self.bills else 0

    def received_subtotal(self) -> int:
        return sum(self.received_qty(line.product) * line.unit_price for line in self.lines)

    @property
    def is_confirmed(self) -> bool:
        return self.status == "dikonfirmasi"

    @property
    def is_fully_received(self) -> bool:
        return bool(self.receipts) and all(
            self.received_qty(line.product) == line.qty for line in self.lines
        )

    @property
    def is_partially_received(self) -> bool:
        return bool(self.receipts) and not self.is_fully_received


class Dataset(_Model):
    version: int = Field(alias="versi")
    description: str = Field(alias="keterangan")
    reference_date: date = Field(alias="tanggal_acuan")
    approval_threshold: int = Field(alias="ambang_persetujuan_rupiah", gt=0)
    purchase_tax: PurchaseTax = Field(alias="pajak_pembelian")
    company: Company = Field(alias="perusahaan")
    banks: tuple[Bank, ...] = Field(alias="bank")
    users: tuple[User, ...] = Field(alias="pengguna")
    vendors: tuple[Vendor, ...] = Field(alias="vendor")
    external_vendors: tuple[ExternalVendor, ...] = Field(alias="vendor_luar_erp")
    products: tuple[Product, ...] = Field(alias="produk")
    purchase_orders: tuple[PurchaseOrder, ...] = Field(alias="purchase_order")

    # -- lookups --------------------------------------------------------------------------------

    @cached_property
    def _banks_by_key(self) -> dict[str, Bank]:
        return {b.key: b for b in self.banks}

    @cached_property
    def _vendors_by_key(self) -> dict[str, Vendor]:
        return {v.key: v for v in self.vendors}

    @cached_property
    def _products_by_key(self) -> dict[str, Product]:
        return {p.key: p for p in self.products}

    @cached_property
    def _pos_by_key(self) -> dict[str, PurchaseOrder]:
        return {po.key: po for po in self.purchase_orders}

    @cached_property
    def _pos_by_number(self) -> dict[str, PurchaseOrder]:
        return {po.number: po for po in self.purchase_orders}

    def bank(self, key: str) -> Bank:
        return self._banks_by_key[key]

    def vendor(self, key: str) -> Vendor:
        return self._vendors_by_key[key]

    def product(self, key: str) -> Product:
        return self._products_by_key[key]

    def po(self, key: str) -> PurchaseOrder:
        return self._pos_by_key[key]

    def po_by_number(self, number: str) -> PurchaseOrder | None:
        return self._pos_by_number.get(number)

    def user(self, key: str) -> User:
        return next(u for u in self.users if u.key == key)

    def pos_of_vendor(self, vendor_key: str) -> list[PurchaseOrder]:
        return [po for po in self.purchase_orders if po.vendor == vendor_key]

    def products_of_vendor(self, vendor_key: str) -> list[Product]:
        return [p for p in self.products if p.vendor == vendor_key]

    @property
    def history_bills(self) -> list[tuple[PurchaseOrder, VendorBill]]:
        return [(po, bill) for po in self.purchase_orders for bill in po.bills]
