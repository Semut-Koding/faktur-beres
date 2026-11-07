"""An ``ErpReader`` over records held in memory (tests and the dataset-backed oracle reader).

It answers exactly like ``OdooErpReader`` is meant to: NPWP compared as digits, names and e-mail
addresses after normalisation, PO numbers after BR-06 normalisation, vendor bills in state draft or
posted only. Records are given once and never change.
"""

from collections.abc import Iterable, Sequence

from faktur_beres.domain.erp import (
    BankAccountRecord,
    BillRecord,
    BillState,
    CompanyRecord,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.domain.normalize import (
    normalize_email,
    normalize_name,
    normalize_po_number,
    tax_id_digits,
)


class InMemoryErpReader:
    def __init__(
        self,
        company: CompanyRecord,
        vendors: Iterable[VendorRecord],
        accounts: Iterable[BankAccountRecord] = (),
        purchase_orders: Iterable[PurchaseOrderRecord] = (),
        bills: Iterable[BillRecord] = (),
    ) -> None:
        self._company = company
        self._vendors = tuple(vendors)
        self._accounts = tuple(accounts)
        self._pos = tuple(purchase_orders)
        self._bills = tuple(bills)

    def company(self) -> CompanyRecord:
        return self._company

    def vendors_by_vat(self, digits: str) -> Sequence[VendorRecord]:
        return tuple(v for v in self._vendors if digits and tax_id_digits(v.vat) == digits)

    def vendors_by_name(self, name: str) -> Sequence[VendorRecord]:
        wanted = normalize_name(name)
        return tuple(v for v in self._vendors if wanted and normalize_name(v.name) == wanted)

    def vendors_by_email(self, email: str) -> Sequence[VendorRecord]:
        wanted = normalize_email(email)
        return tuple(v for v in self._vendors if wanted and normalize_email(v.email) == wanted)

    def bank_accounts(self, vendor_id: int) -> Sequence[BankAccountRecord]:
        return tuple(a for a in self._accounts if a.partner_id == vendor_id)

    def bank_account_owners(self, digits: str) -> Sequence[BankAccountRecord]:
        return tuple(a for a in self._accounts if digits and a.digits == digits)

    def purchase_order(self, number: str) -> PurchaseOrderRecord | None:
        wanted = normalize_po_number(number)
        found = [po for po in self._pos if normalize_po_number(po.number) == wanted]
        return found[0] if len(found) == 1 else None

    def confirmed_purchase_orders(self, vendor_id: int) -> Sequence[PurchaseOrderRecord]:
        return tuple(po for po in self._pos if po.vendor_id == vendor_id and po.confirmed)

    def vendor_bills(self, vendor_id: int) -> Sequence[BillRecord]:
        counted = (BillState.DRAFT, BillState.POSTED)
        return tuple(b for b in self._bills if b.vendor_id == vendor_id and b.state in counted)

    def posted_bill_count(self, vendor_id: int) -> int:
        return sum(
            1 for b in self._bills if b.vendor_id == vendor_id and b.state == BillState.POSTED
        )
