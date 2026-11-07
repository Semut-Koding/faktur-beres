"""The in-memory ErpReader answers with the same normalisation as the rules."""

from factories import COMPANY, OTHER_ACCOUNT, OTHER_VENDOR, VENDOR, VENDOR_ACCOUNT, bill, po
from faktur_beres.domain.erp import BillState, ErpReader, PoState
from faktur_beres.evals.memory_erp import InMemoryErpReader


def reader() -> InMemoryErpReader:
    return InMemoryErpReader(
        COMPANY,
        [VENDOR, OTHER_VENDOR],
        [VENDOR_ACCOUNT, OTHER_ACCOUNT],
        [po(), po(po_id=901, number="P00901", state=PoState.CANCEL)],
        [bill(), bill(bill_id=71, state=BillState.CANCEL), bill(bill_id=72, state=BillState.DRAFT)],
    )


def test_is_an_erp_reader() -> None:
    erp: ErpReader = reader()
    assert erp.company() == COMPANY


def test_lookups_normalise() -> None:
    erp = reader()
    assert erp.vendors_by_vat("0990427957220000") == (VENDOR,)
    assert erp.vendors_by_vat("") == ()
    assert erp.vendors_by_name("p.t. pemasok   FIKTIF") == (VENDOR,)
    assert erp.vendors_by_email("Tagihan <TAGIHAN@pemasok.test>") == (VENDOR,)
    assert erp.bank_accounts(VENDOR.id) == (VENDOR_ACCOUNT,)
    assert erp.bank_account_owners("1897077695") == (OTHER_ACCOUNT,)
    assert erp.purchase_order(" p00900") is not None
    assert erp.purchase_order("P00999") is None


def test_purchase_orders_and_bills() -> None:
    erp = reader()
    assert [p.number for p in erp.confirmed_purchase_orders(VENDOR.id)] == ["P00900"]
    assert erp.purchase_order("P00901") is not None  # any state by number
    assert [b.id for b in erp.vendor_bills(VENDOR.id)] == [70, 72]
    assert erp.posted_bill_count(VENDOR.id) == 1
    assert erp.posted_bill_count(OTHER_VENDOR.id) == 0
