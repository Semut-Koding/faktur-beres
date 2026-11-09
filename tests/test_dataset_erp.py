"""DatasetErpReader rebuilds the seeded ERP state from the dataset (offline oracle)."""

import pytest

from faktur_beres.dataset import load_dataset
from faktur_beres.domain.erp import BillState, ErpReader, PoState
from faktur_beres.evals.dataset_erp import DatasetErpReader


@pytest.fixture(scope="module")
def ds():
    return load_dataset()


@pytest.fixture(scope="module")
def erp(ds) -> DatasetErpReader:
    return DatasetErpReader(ds)


def test_is_an_erp_reader(erp: DatasetErpReader) -> None:
    reader: ErpReader = erp
    assert reader.company().vat == "0990229163926000"


def test_registered_vendors_and_accounts_only(ds, erp: DatasetErpReader) -> None:
    for vendor in ds.vendors:
        (record,) = erp.vendors_by_vat(vendor.npwp)
        assert record.name == vendor.name and record.email == vendor.billing_email
        (account,) = erp.bank_accounts(record.id)
        assert account.digits == vendor.bank_account.number
        assert account.bank_name == ds.bank(vendor.bank_account.bank).name
    for outside in ds.external_vendors:
        assert erp.vendors_by_vat(outside.npwp) == ()
        assert erp.vendors_by_name(outside.name) == ()
        assert erp.bank_account_owners(outside.bank_account.number) == ()


def test_purchase_orders_and_quantities(ds, erp: DatasetErpReader) -> None:
    assert sum(1 for po in ds.purchase_orders if erp.purchase_order(po.number)) == 56
    p27 = erp.purchase_order("P00027")
    assert p27 is not None and p27.state == PoState.PURCHASE
    (line,) = p27.lines
    assert (line.quantity, line.qty_received, line.qty_invoiced, line.vendor_codes) == (
        2100,
        1680,
        0,
        ("KL-200",),
    )
    assert erp.purchase_order("P00013").state == PoState.CANCEL
    assert erp.purchase_order("P00053").state == PoState.DRAFT
    assert erp.purchase_order("P00330") is None
    p10 = erp.purchase_order("P00010")
    assert [ln.open_quantity for ln in p10.lines] == [0, 0]


def test_history_bills(ds, erp: DatasetErpReader) -> None:
    bills = [b for v in ds.vendors for b in erp.vendor_bills(erp.vendors_by_vat(v.npwp)[0].id)]
    assert len(bills) == len(ds.history_bills) == 14
    assert {b.state for b in bills} == {BillState.POSTED}
    by_ref = {b.ref: b for b in bills}
    for po, bill in ds.history_bills:
        record = by_ref[bill.vendor_invoice_number]
        assert record.invoice_date == bill.invoice_date and record.amount_total == po.total()
        assert sum(ln.quantity for ln in record.lines) == sum(
            po.billed_qty(ln.product) for ln in po.lines
        )
    v09 = erp.vendors_by_vat(ds.vendor("v09").npwp)[0]
    assert erp.posted_bill_count(v09.id) == 0


def test_registered_banks(ds, erp: DatasetErpReader) -> None:
    assert [b.name for b in erp.banks()] == [b.name for b in ds.banks]
