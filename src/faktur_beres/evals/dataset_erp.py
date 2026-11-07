"""The seeded ERP state rebuilt from ``data/seed/dataset.yaml`` (offline oracle, evaluation only).

It holds what ``npm run odoo:seed`` puts in Odoo: the company, the registered vendors (not the
vendor outside the ERP), their bank accounts, the products with the vendors' product codes, the 56
POs with quantities received and invoiced, and the 14 posted history bills with their lines. Ids
are local and stable; scoring maps records back to dataset keys through natural keys (NPWP,
product code, PO number), so the same scoring works for ``OdooErpReader``.
"""

from decimal import Decimal

from faktur_beres.dataset import Dataset
from faktur_beres.domain.erp import (
    BankAccountRecord,
    BillLineRecord,
    BillRecord,
    BillState,
    CompanyRecord,
    PoState,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.evals.memory_erp import InMemoryErpReader

PO_STATES = {"dikonfirmasi": PoState.PURCHASE, "batal": PoState.CANCEL, "rfq": PoState.DRAFT}
UOM_IDS = {"kg": 12, "Units": 1}
TAX_ID = 1
CURRENCY_ID = 12  # IDR
PAYMENT_TERM_ID = 4  # 30 days


class DatasetErpReader(InMemoryErpReader):
    def __init__(self, ds: Dataset) -> None:
        company = CompanyRecord(id=1, name=ds.company.name, vat=ds.company.npwp)
        vendor_ids = {v.key: 1000 + i for i, v in enumerate(ds.vendors, start=1)}
        vendors = [
            VendorRecord(id=vendor_ids[v.key], name=v.name, vat=v.npwp, email=v.billing_email)
            for v in ds.vendors
        ]
        accounts = [
            BankAccountRecord(
                id=2000 + i,
                partner_id=vendor_ids[v.key],
                number=v.bank_account.number,
                bank_name=ds.bank(v.bank_account.bank).name,
                holder=v.bank_account.holder,
            )
            for i, v in enumerate(ds.vendors, start=1)
        ]
        product_ids = {p.key: 3000 + i for i, p in enumerate(ds.products, start=1)}

        pos: list[PurchaseOrderRecord] = []
        bills: list[BillRecord] = []
        line_id = 5000
        for i, po in enumerate(ds.purchase_orders, start=1):
            lines = []
            line_ids: dict[str, int] = {}
            for line in po.lines:
                line_id += 1
                line_ids[line.product] = line_id
                product = ds.product(line.product)
                lines.append(
                    PurchaseOrderLineRecord(
                        id=line_id,
                        product_id=product_ids[product.key],
                        product_code=product.code,
                        product_name=product.name,
                        vendor_codes=(product.vendor_code,) if product.vendor == po.vendor else (),
                        description=f"[{product.code}] {product.name}",
                        uom_id=UOM_IDS[product.uom],
                        uom_name=product.uom,
                        quantity=Decimal(line.qty),
                        qty_received=Decimal(po.received_qty(line.product)),
                        qty_invoiced=Decimal(po.billed_qty(line.product)),
                        price_unit=Decimal(line.unit_price),
                        tax_ids=(TAX_ID,),
                    )
                )
            pos.append(
                PurchaseOrderRecord(
                    id=4000 + i,
                    number=po.number,
                    vendor_id=vendor_ids[po.vendor],
                    state=PO_STATES[po.status],
                    currency_id=CURRENCY_ID,
                    payment_term_id=PAYMENT_TERM_ID,
                    lines=tuple(lines),
                )
            )
            for bill in po.bills:
                bills.append(
                    BillRecord(
                        id=6000 + len(bills) + 1,
                        vendor_id=vendor_ids[po.vendor],
                        ref=bill.vendor_invoice_number,
                        invoice_date=bill.invoice_date,
                        state=BillState.POSTED,
                        amount_total=Decimal(po.total()),
                        lines=tuple(
                            BillLineRecord(
                                id=7000 + len(bills) * 10 + j,
                                purchase_line_id=line_ids[line.product],
                                product_id=product_ids[line.product],
                                quantity=Decimal(po.billed_qty(line.product)),
                                price_unit=Decimal(line.unit_price),
                            )
                            for j, line in enumerate(po.lines, start=1)
                        ),
                    )
                )
        super().__init__(company, vendors, accounts, pos, bills)
