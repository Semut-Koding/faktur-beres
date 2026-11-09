"""An ``ErpReader`` that writes one audit row per read (BR-23), with masked parameters (BR-24)."""

import time
from collections.abc import Callable, Sequence
from typing import Any

from faktur_beres.db.models import AuditKind
from faktur_beres.domain.erp import (
    BankAccountRecord,
    BankRecord,
    BillRecord,
    CompanyRecord,
    ErpReader,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.graph.deps import GraphStore


class AuditedReader:
    def __init__(self, reader: ErpReader, store: GraphStore, invoice_id: str, node: str) -> None:
        self._reader = reader
        self._store = store
        self._invoice_id = invoice_id
        self._node = node

    def _call[T](self, name: str, params: dict[str, Any], fn: Callable[[], T]) -> T:
        started = time.monotonic()
        status = "ok"
        result: T | None = None
        try:
            result = fn()
            return result
        except Exception as exc:
            status = type(exc).__name__
            raise
        finally:
            summary = (
                f"{len(result)} baris"
                if isinstance(result, list | tuple)
                else ("ada" if result is not None else "tidak ada")
            )
            self._store.audit(
                self._invoice_id,
                kind=AuditKind.TOOL,
                name=name,
                node=self._node,
                params=params,
                summary=summary,
                duration_ms=int((time.monotonic() - started) * 1000),
                status=status,
            )

    def company(self) -> CompanyRecord:
        return self._call("company", {}, self._reader.company)

    def vendors_by_vat(self, digits: str) -> Sequence[VendorRecord]:
        return self._call(
            "vendors_by_vat", {"npwp": digits}, lambda: self._reader.vendors_by_vat(digits)
        )

    def vendors_by_name(self, name: str) -> Sequence[VendorRecord]:
        return self._call(
            "vendors_by_name", {"name": name}, lambda: self._reader.vendors_by_name(name)
        )

    def vendors_by_email(self, email: str) -> Sequence[VendorRecord]:
        return self._call(
            "vendors_by_email", {"email": email}, lambda: self._reader.vendors_by_email(email)
        )

    def bank_accounts(self, vendor_id: int) -> Sequence[BankAccountRecord]:
        return self._call(
            "bank_accounts", {"vendor_id": vendor_id}, lambda: self._reader.bank_accounts(vendor_id)
        )

    def banks(self) -> Sequence[BankRecord]:
        return self._call("banks", {}, self._reader.banks)

    def bank_account_owners(self, digits: str) -> Sequence[BankAccountRecord]:
        return self._call(
            "bank_account_owners",
            {"rekening": digits},
            lambda: self._reader.bank_account_owners(digits),
        )

    def purchase_order(self, number: str) -> PurchaseOrderRecord | None:
        return self._call(
            "purchase_order", {"number": number}, lambda: self._reader.purchase_order(number)
        )

    def confirmed_purchase_orders(self, vendor_id: int) -> Sequence[PurchaseOrderRecord]:
        return self._call(
            "confirmed_purchase_orders",
            {"vendor_id": vendor_id},
            lambda: self._reader.confirmed_purchase_orders(vendor_id),
        )

    def vendor_bills(self, vendor_id: int) -> Sequence[BillRecord]:
        return self._call(
            "vendor_bills", {"vendor_id": vendor_id}, lambda: self._reader.vendor_bills(vendor_id)
        )

    def posted_bill_count(self, vendor_id: int) -> int:
        return self._call(
            "posted_bill_count",
            {"vendor_id": vendor_id},
            lambda: self._reader.posted_bill_count(vendor_id),
        )
