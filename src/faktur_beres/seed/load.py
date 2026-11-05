"""``npm run odoo:seed``: load data/seed/dataset.yaml into Odoo through JSON-2 (admin key).

Idempotent. Every seeded record gets an external ID in module ``fb_seed`` with a neutral name
(``vendor_v01``, ``product_p01``, ``po_o11``, ``bill_h10``). On a re-run a record that already
exists is skipped and any difference from the dataset is reported, never overwritten; records
found by their natural key without an external ID are adopted. The seed never deletes anything.
Scenario IDs are never written to Odoo (ADR-0007).
"""

import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from faktur_beres.dataset import Dataset, PurchaseOrder, Receipt, get_dataset
from faktur_beres.odoo import OdooClient, OdooError
from faktur_beres.seed.common import SeedError, XmlIds, admin_client, m2o_id, odoo_datetime
from faktur_beres.textui import print_table

CREATED, EXISTS, ADOPTED = "dibuat", "sudah ada", "diadopsi"
UOM_XMLIDS = {"kg": "uom.product_uom_kgm", "Units": "uom.product_uom_unit"}
PAYMENT_TERM_XMLIDS = {30: "account.account_payment_term_30days"}
TAX_XMLID = "fb_seed.tax_ppn_masukan"
LANG = "id_ID"
TZ = "Asia/Jakarta"
# JSON-2 runs a call with exactly the context it is given (empty when omitted), so actions get
# the language and time zone explicitly.
ACTION_CONTEXT = {"lang": LANG, "tz": TZ}
PO_STATE = {"dikonfirmasi": "purchase", "batal": "cancel", "rfq": "draft"}
QTY_EPSILON = 1e-6


def planned_date(po: PurchaseOrder):
    """Planned receipt date: the dataset value, else the actual receipt date, else the order."""
    if po.planned_receipt_date:
        return po.planned_receipt_date
    if po.receipts:
        return po.receipts[0].received_on
    return po.order_date


DEFAULT_STEPS = (
    "banks",
    "vendors",
    "products",
    "purchase_orders",
    "receipts",
    "history_bills",
)


class Seeder:
    def __init__(self, client: OdooClient, ds: Dataset) -> None:
        self.c = client
        self.ds = ds
        self.x = XmlIds(client)
        self.stats: dict[str, Counter[str]] = defaultdict(Counter)
        self.diffs: list[str] = []
        self.ids: dict[str, int] = {}  # dataset key -> Odoo id (vendors, banks, products)
        self.po_ids: dict[str, int] = {}  # PO key -> purchase.order id
        self.terms: dict[str, int] = {}  # vendor key -> account.payment.term id

    # -- helpers ------------------------------------------------------------------------------

    def _count(self, kind: str, status: str) -> None:
        self.stats[kind][status] += 1

    def _exists(self, model: str, res_id: int | None) -> bool:
        return bool(res_id) and self.c.search_count(model, [["id", "=", res_id]]) == 1

    def _compare(self, label: str, record: dict[str, Any], expected: dict[str, Any]) -> None:
        for field, want in expected.items():
            have = record.get(field)
            if isinstance(have, list) and field.endswith("_id"):
                have = m2o_id(have)
            if have is False:
                have = None
            if have != want:
                self.diffs.append(f"{label}: {field} di Odoo {have!r}, dataset {want!r}")

    def _ensure(
        self,
        kind: str,
        xmlid: str,
        model: str,
        natural: list,
        values: dict[str, Any],
        compare: dict[str, Any],
        *,
        context: dict | None = None,
    ) -> tuple[int, str]:
        """Return the id for ``xmlid``: existing, adopted by natural key, or newly created."""
        res_id = self.x.get(xmlid, model)
        status = EXISTS
        if not self._exists(model, res_id):
            found = self.c.search(model, natural, limit=2)
            if len(found) > 1:
                raise SeedError(f"{kind} {xmlid}: lebih dari satu record cocok {natural}")
            if found:
                res_id, status = found[0], ADOPTED
            else:
                (res_id,) = self.c.create(model, values, context=context)
                status = CREATED
            self.x.register(xmlid, model, res_id)
        if status != CREATED and compare:
            record = self.c.read(model, [res_id], list(compare))[0]
            self._compare(f"{self.x.module}.{xmlid}", record, compare)
        self._count(kind, status)
        return res_id, status

    # -- reference data -----------------------------------------------------------------------

    def load_refs(self) -> None:
        self.country_id = self.x.ref("base.id", "res.country")
        self.currency_id = self.x.ref("base.IDR", "res.currency")
        self.tax_id = self.x.ref(TAX_XMLID, "account.tax")
        self.uom_ids = {name: self.x.ref(xmlid, "uom.uom") for name, xmlid in UOM_XMLIDS.items()}
        states = self.c.search_read(
            "res.country.state", [["country_id", "=", self.country_id]], ["name"]
        )
        self.states = {s["name"].lower(): s["id"] for s in states}

    def state_id(self, name: str) -> int:
        key = name.lower()
        if key in self.states:
            return self.states[key]
        # Odoo names some provinces without their prefix (e.g. "DKI Jakarta" -> "Jakarta").
        matches = [sid for sname, sid in self.states.items() if sname in key]
        if len(matches) != 1:
            raise SeedError(f"provinsi '{name}' tidak ditemukan di Odoo")
        return matches[0]

    # -- master data ----------------------------------------------------------------------------

    def seed_banks(self) -> None:
        for bank in self.ds.banks:
            values = {"name": bank.name}
            self.ids[bank.key], _ = self._ensure(
                "bank", bank.key, "res.bank", [["name", "=", bank.name]], values, values
            )

    def seed_vendors(self) -> None:
        for v in self.ds.vendors:
            term = PAYMENT_TERM_XMLIDS.get(v.payment_term_days)
            if term is None:
                raise SeedError(f"{v.key}: termin {v.payment_term_days} hari belum didukung")
            values = {
                "name": v.name,
                "is_company": True,
                "vat": v.npwp,
                "street": v.street,
                "city": v.city,
                "zip": v.zip,
                "state_id": self.state_id(v.state),
                "country_id": self.country_id,
                "email": v.billing_email,
                "lang": LANG,
                "supplier_rank": 1,
                "property_supplier_payment_term_id": self.x.ref(term, "account.payment.term"),
            }
            self.terms[v.key] = values["property_supplier_payment_term_id"]
            compare = {k: values[k] for k in ("name", "vat", "email", "street", "city", "zip")}
            compare["state_id"] = values["state_id"]
            vendor_id, _ = self._ensure(
                "vendor",
                f"vendor_{v.key}",
                "res.partner",
                [["vat", "=", v.npwp], ["is_company", "=", True]],
                values,
                compare,
            )
            self.ids[v.key] = vendor_id

            account = v.bank_account
            acc_values = {
                "acc_number": account.number,
                "bank_id": self.ids[account.bank],
                "partner_id": vendor_id,
                "acc_holder_name": account.holder,
            }
            self._ensure(
                "rekening",
                f"bank_account_{v.key}",
                "res.partner.bank",
                [["acc_number", "=", account.number], ["partner_id", "=", vendor_id]],
                acc_values,
                acc_values,
            )

    def seed_products(self) -> None:
        for p in self.ds.products:
            vendor_id = self.ids[p.vendor]
            seller = {
                "partner_id": vendor_id,
                "product_code": p.vendor_code,
                "price": float(p.purchase_price),
                "currency_id": self.currency_id,
                "min_qty": 0.0,
            }
            values = {
                "name": p.name,
                "default_code": p.code,
                "type": "consu",
                "is_storable": True,
                "uom_id": self.uom_ids[p.uom],
                "purchase_method": "receive",
                "purchase_ok": True,
                "sale_ok": False,
                "standard_price": float(p.purchase_price),
                "supplier_taxes_id": [(6, 0, [self.tax_id])],
                "taxes_id": [(6, 0, [])],
                "seller_ids": [(0, 0, seller)],
            }
            compare = {
                "name": p.name,
                "default_code": p.code,
                "uom_id": self.uom_ids[p.uom],
                "purchase_method": "receive",
                "is_storable": True,
            }
            tmpl_id, status = self._ensure(
                "produk",
                f"product_template_{p.key}",
                "product.template",
                [["default_code", "=", p.code]],
                values,
                compare,
            )
            variants = self.c.search("product.product", [["product_tmpl_id", "=", tmpl_id]])
            if len(variants) != 1:
                raise SeedError(f"{p.key}: produk harus punya tepat satu varian")
            if self.x.get(f"product_{p.key}", "product.product") is None:
                self.x.register(f"product_{p.key}", "product.product", variants[0])
            self.ids[p.key] = variants[0]

            if status != CREATED:
                taxes = self.c.read("product.template", [tmpl_id], ["supplier_taxes_id"])[0]
                if taxes["supplier_taxes_id"] != [self.tax_id]:
                    self.diffs.append(f"produk {p.key}: pajak pembelian berbeda dari {TAX_XMLID}")
            sellers = self.c.search(
                "product.supplierinfo",
                [["product_tmpl_id", "=", tmpl_id], ["partner_id", "=", vendor_id]],
            )
            if status == CREATED or not sellers:
                if not sellers:
                    (sid,) = self.c.create(
                        "product.supplierinfo", {**seller, "product_tmpl_id": tmpl_id}
                    )
                    sellers = [sid]
                if self.x.get(f"supplierinfo_{p.key}", "product.supplierinfo") is None:
                    self.x.register(f"supplierinfo_{p.key}", "product.supplierinfo", sellers[0])
                self._count("info pemasok", CREATED)
            else:
                info = self.c.read("product.supplierinfo", sellers[:1], ["product_code", "price"])
                self._compare(
                    f"info pemasok {p.key}",
                    {**info[0], "price": int(info[0]["price"])},
                    {"product_code": p.vendor_code, "price": p.purchase_price},
                )
                if self.x.get(f"supplierinfo_{p.key}", "product.supplierinfo") is None:
                    self.x.register(f"supplierinfo_{p.key}", "product.supplierinfo", sellers[0])
                self._count("info pemasok", EXISTS)

    # -- purchase orders ------------------------------------------------------------------------

    def seed_purchase_orders(self) -> None:
        for po in self.ds.purchase_orders:
            vendor_id = self.ids[po.vendor]
            planned = odoo_datetime(planned_date(po))
            values = {
                "name": po.number,
                "partner_id": vendor_id,
                # The UI fills this from the vendor through an onchange, which API calls skip.
                "payment_term_id": self.terms[po.vendor],
                "date_order": odoo_datetime(po.order_date),
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.ids[line.product],
                            "product_qty": float(line.qty),
                            "price_unit": float(line.unit_price),
                            "tax_ids": [(6, 0, [self.tax_id])],
                            "date_planned": planned,
                        },
                    )
                    for line in po.lines
                ],
            }
            po_id, status = self._ensure(
                "purchase order",
                f"po_{po.key}",
                "purchase.order",
                [["name", "=", po.number]],
                values,
                {"partner_id": vendor_id, "payment_term_id": self.terms[po.vendor]},
            )
            self.po_ids[po.key] = po_id
            if status != CREATED:
                self._compare_po_lines(po, po_id)
            self._apply_po_state(po, po_id)

    def _po_lines(self, po_id: int) -> dict[int, dict[str, Any]]:
        lines = self.c.search_read(
            "purchase.order.line",
            [["order_id", "=", po_id], ["display_type", "=", False]],
            ["product_id", "product_qty", "price_unit", "qty_received", "qty_invoiced", "tax_ids"],
        )
        return {m2o_id(line["product_id"]): line for line in lines}

    def _compare_po_lines(self, po: PurchaseOrder, po_id: int) -> None:
        have = {
            pid: (line["product_qty"], line["price_unit"])
            for pid, line in self._po_lines(po_id).items()
        }
        want = {
            self.ids[line.product]: (float(line.qty), float(line.unit_price)) for line in po.lines
        }
        if have != want:
            self.diffs.append(f"{po.number}: baris PO di Odoo berbeda dari dataset")

    def _apply_po_state(self, po: PurchaseOrder, po_id: int) -> None:
        state = self.c.read("purchase.order", [po_id], ["state"])[0]["state"]
        target = PO_STATE[po.status]
        if state == target:
            return
        if state not in ("draft", "sent"):
            self.diffs.append(f"{po.number}: status Odoo {state}, dataset {po.status}")
            return
        if po.status == "dikonfirmasi":
            self.c.call("purchase.order", "button_confirm", ids=[po_id], context=ACTION_CONTEXT)
            # Confirmation stamps date_approve with "now"; use the dataset order date instead.
            self.c.write("purchase.order", [po_id], {"date_approve": odoo_datetime(po.order_date)})
            self._count("PO dikonfirmasi", CREATED)
        elif po.status == "batal":
            self.c.call("purchase.order", "button_cancel", ids=[po_id], context=ACTION_CONTEXT)
            self._count("PO dibatalkan", CREATED)
        state = self.c.read("purchase.order", [po_id], ["state"])[0]["state"]
        if state != target:
            raise SeedError(f"{po.number}: status menjadi {state}, seharusnya {target}")

    # -- receipts -------------------------------------------------------------------------------

    def seed_receipts(self) -> None:
        for po in self.ds.purchase_orders:
            if not po.is_confirmed:
                continue
            po_id = self.po_ids[po.key]
            lines = self._po_lines(po_id)
            want = {self.ids[x.product]: float(po.received_qty(x.product)) for x in po.lines}
            have = {pid: lines[pid]["qty_received"] for pid in want}
            if all(abs(have[pid] - want[pid]) < QTY_EPSILON for pid in want):
                self.stats["penerimaan"][EXISTS] += len(po.receipts)
                continue
            if any(have.values()):
                self.diffs.append(f"{po.number}: qty diterima di Odoo {have}, dataset {want}")
                continue
            for receipt in po.receipts:
                self._validate_receipt(po, po_id, receipt)
                self._count("penerimaan", CREATED)
                if receipt.remainder == "backorder":
                    self._count("backorder", CREATED)

    def _validate_receipt(self, po: PurchaseOrder, po_id: int, receipt: Receipt) -> None:
        picking_ids = self.c.read("purchase.order", [po_id], ["picking_ids"])[0]["picking_ids"]
        open_pickings = self.c.search(
            "stock.picking", [["id", "in", picking_ids], ["state", "not in", ["done", "cancel"]]]
        )
        if len(open_pickings) != 1:
            raise SeedError(f"{po.number}: harus ada tepat satu penerimaan terbuka")
        picking_id = open_pickings[0]
        key_of = {self.ids[key]: key for key in receipt.qty}
        moves = self.c.search_read("stock.move", [["picking_id", "=", picking_id]], ["product_id"])
        for move in moves:
            qty = receipt.qty.get(key_of.get(m2o_id(move["product_id"]), ""), 0)
            self.c.write("stock.move", [move["id"]], {"quantity": float(qty), "picked": qty > 0})
        # skip_backorder: no wizard; the unreceived remainder becomes an open backorder.
        result = self.c.call(
            "stock.picking",
            "button_validate",
            ids=[picking_id],
            context={**ACTION_CONTEXT, "skip_backorder": True},
        )
        if isinstance(result, dict) and result.get("res_model"):
            raise SeedError(
                f"{po.number}: validasi penerimaan membuka wizard {result['res_model']}"
            )
        state = self.c.read("stock.picking", [picking_id], ["state"])[0]["state"]
        if state != "done":
            raise SeedError(f"{po.number}: penerimaan berstatus {state}, bukan done")
        # Validation stamps "now"; rewrite to the dataset receipt date.
        when = odoo_datetime(receipt.received_on)
        self.c.write("stock.picking", [picking_id], {"date_done": when})
        done_moves = self.c.search(
            "stock.move", [["picking_id", "=", picking_id], ["state", "=", "done"]]
        )
        self.c.write("stock.move", done_moves, {"date": when})
        move_lines = self.c.search("stock.move.line", [["move_id", "in", done_moves]])
        self.c.write("stock.move.line", move_lines, {"date": when})

    # -- history bills --------------------------------------------------------------------------

    def seed_history_bills(self) -> None:
        for po, bill in self.ds.history_bills:
            po_id = self.po_ids[po.key]
            vendor_id = self.ids[po.vendor]
            xmlid = f"bill_{po.key}"
            move_id = self.x.get(xmlid, "account.move")
            status = EXISTS
            if not self._exists("account.move", move_id):
                found = self.c.search(
                    "account.move",
                    [
                        ["move_type", "=", "in_invoice"],
                        ["partner_id", "=", vendor_id],
                        ["ref", "=", bill.vendor_invoice_number],
                    ],
                )
                if not found:
                    # A draft left behind by an interrupted run.
                    found = self.c.search(
                        "account.move",
                        [
                            ["move_type", "=", "in_invoice"],
                            ["partner_id", "=", vendor_id],
                            ["invoice_origin", "=", po.number],
                            ["state", "=", "draft"],
                        ],
                    )
                if len(found) > 1:
                    raise SeedError(f"{po.number}: lebih dari satu tagihan cocok")
                if found:
                    move_id, status = found[0], ADOPTED
                else:
                    action = self.c.call(
                        "purchase.order",
                        "action_create_invoice",
                        ids=[po_id],
                        context=ACTION_CONTEXT,
                    )
                    move_id = action.get("res_id") if isinstance(action, dict) else None
                    if not move_id:
                        raise SeedError(f"{po.number}: tagihan tidak terbentuk dari PO")
                    status = CREATED
                self.x.register(xmlid, "account.move", move_id)

            fields = ["state", "ref", "invoice_date", "date", "amount_total"]
            record = self.c.read("account.move", [move_id], fields)[0]
            if record["state"] == "draft":
                # Odoo books a bill entered in a later month on the last day of the invoice
                # month; these history bills are booked on their invoice date instead.
                self.c.write(
                    "account.move",
                    [move_id],
                    {
                        "ref": bill.vendor_invoice_number,
                        "invoice_date": bill.invoice_date,
                        "date": bill.invoice_date,
                    },
                    context=ACTION_CONTEXT,
                )
                self.c.call("account.move", "action_post", ids=[move_id], context=ACTION_CONTEXT)
                record = self.c.read("account.move", [move_id], fields)[0]
            elif status != CREATED:
                self._compare(
                    f"{self.x.module}.{xmlid}",
                    record,
                    {
                        "ref": bill.vendor_invoice_number,
                        "invoice_date": str(bill.invoice_date),
                        "date": str(bill.invoice_date),
                    },
                )
            if record["state"] != "posted":
                raise SeedError(f"{po.number}: tagihan berstatus {record['state']}")
            if round(record["amount_total"]) != po.total():
                self.diffs.append(
                    f"{xmlid}: total Odoo {record['amount_total']}, dataset {po.total()}"
                )
            self._count("tagihan riwayat", status)

    # -- run ------------------------------------------------------------------------------------

    def run(self, steps: Iterable[str] | None = None) -> None:
        self.load_refs()
        for step in steps or DEFAULT_STEPS:
            getattr(self, f"seed_{step}")()


SUMMARY_MODELS = (
    ("Bank", "res.bank"),
    ("Vendor", "res.partner"),
    ("Rekening vendor", "res.partner.bank"),
    ("Produk", "product.product"),
    ("Info pemasok", "product.supplierinfo"),
    ("Purchase order", "purchase.order"),
    ("Tagihan riwayat", "account.move"),
)


def summary_counts(client: OdooClient) -> list[tuple[str, int]]:
    """Number of records carrying an fb_seed external ID, per model."""
    per_model = Counter(model for model, _ in XmlIds(client).names().values())
    return [(label, per_model[model]) for label, model in SUMMARY_MODELS]


def main() -> int:
    ds = get_dataset()
    try:
        with admin_client() as client:
            seeder = Seeder(client, ds)
            seeder.run()
            rows = []
            for kind, counter in seeder.stats.items():
                rows.append((kind, counter[CREATED], counter[EXISTS], counter[ADOPTED]))
            print("== Hasil seed per jenis")
            print_table(("Jenis", "Dibuat", "Sudah ada", "Diadopsi"), rows)
            if seeder.diffs:
                print(f"\n== Perbedaan dengan dataset ({len(seeder.diffs)}), tidak ditimpa:")
                for line in seeder.diffs:
                    print(f"  - {line}")
            else:
                print("\n== Perbedaan dengan dataset: tidak ada")
            print("\n== Ringkasan record seed di Odoo")
            print_table(("Jenis", "Jumlah"), summary_counts(client))
    except (SeedError, OdooError) as exc:
        print(f"galat: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
