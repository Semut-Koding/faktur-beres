"""``npm run odoo:check``: compare Odoo with the dataset and the data needs of SPEC §15.3.

Prints ``Pemeriksaan | Hasil | Status`` and exits non-zero when any row fails. Expected numbers
are computed from the dataset, not hard-coded. Reads with the admin key; the agent's rights are
checked with the agent key and ``has_access`` only (the matrix of RENCANA-TEKNIS §4.4: ACL level
on empty recordsets, record-rule level on seed records). Nothing is created, deleted, or posted;
the refusals themselves are exercised by ``npm run test:integrasi``.
"""

import ast
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from faktur_beres.config import REPO_ROOT, Settings
from faktur_beres.dataset import Dataset, get_dataset
from faktur_beres.odoo import OdooClient, OdooError
from faktur_beres.seed.common import SeedError, XmlIds, admin_client, m2o_id
from faktur_beres.seed.init import AGENT_GROUPS, API_KEY_DAYS, access_params
from faktur_beres.seed.load import MIN_NEXT_PO_NUMBER, PO_STATE, TAX_XMLID
from faktur_beres.seed.runtime import (
    FORBIDDEN_CONTAINER_ENV,
    container_env_names,
    forbidden_env,
    odoo_conf_option,
)
from faktur_beres.textui import print_table

OK, FAIL, INFO = "OK", "GAGAL", "INFO"
LABEL_PATTERN = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")
EPS = 1e-6
PRICE_TOLERANCE_PERCENT = 1  # BR-08

# Text fields scanned for scenario IDs (only those that exist in this Odoo are read).
LEAK_FIELDS = {
    "res.company": ["name", "email", "website"],
    "res.users": ["name", "login"],
    "res.bank": ["name"],
    "res.partner": ["name", "ref", "comment", "email", "street", "city"],
    "res.partner.bank": ["acc_number", "acc_holder_name"],
    "product.template": ["name", "default_code", "description", "description_purchase"],
    "product.supplierinfo": ["product_code", "product_name"],
    "purchase.order": ["name", "origin", "partner_ref", "note"],
    "purchase.order.line": ["name"],
    "stock.picking": ["name", "origin", "note"],
    "account.move": ["name", "ref", "narration", "invoice_origin", "payment_reference"],
    "account.move.line": ["name"],
    "account.tax": ["name", "description", "invoice_label"],
    "mail.message": ["subject", "body"],
}
AGENT_ADDON = "faktur_beres_agent"
AGENT_MANIFEST = REPO_ROOT / "infra" / "odoo" / "addons" / AGENT_ADDON / "__manifest__.py"
INBOX_GROUP = "mail.group_mail_notification_type_inbox"
#: Groups that would give the agent posting rights or rules over all vendor bills (M2.1).
FORBIDDEN_AGENT_GROUPS = (
    "account.group_account_invoice",
    "account.group_account_basic",
    "account.group_account_user",
    "account.group_account_manager",
    "purchase.group_purchase_user",
    "stock.group_stock_user",
)
OPERATIONS = ("read", "create", "write", "unlink")
OPERATION_LABELS = {"read": "baca", "create": "buat", "write": "ubah", "unlink": "hapus"}
READ_ONLY = ("read",)
#: ACL level (``has_access`` on an empty recordset): operations the agent's groups allow.
#: stock.move.line, ir.attachment, and mail.message get full CRUD from base.group_user; record
#: rules of the addon close what the agent must not do (see AGENT_RULE_CLOSED).
AGENT_ACL: dict[str, tuple[str, ...]] = {
    "res.company": READ_ONLY,
    "res.partner": READ_ONLY,
    "res.partner.bank": READ_ONLY,
    "res.bank": READ_ONLY,
    "product.product": READ_ONLY,
    "product.template": READ_ONLY,
    "product.supplierinfo": READ_ONLY,
    "uom.uom": READ_ONLY,
    "purchase.order": READ_ONLY,
    "purchase.order.line": READ_ONLY,
    "stock.picking": READ_ONLY,
    "stock.move": READ_ONLY,
    "stock.move.line": OPERATIONS,
    "account.tax": READ_ONLY,
    "account.journal": READ_ONLY,
    "account.payment.term": READ_ONLY,
    "res.currency": READ_ONLY,
    "account.move": ("read", "create", "write"),
    "account.move.line": ("read", "create", "write"),
    "ir.attachment": OPERATIONS,
    "mail.message": OPERATIONS,
}
AGENT_RULE_CLOSED = {
    "stock.move.line": "buat, ubah, hapus ditutup aturan",
    "ir.attachment": "hapus ditutup aturan; buat hanya pada draft agent",
    "mail.message": "hapus ditutup aturan; buat lewat message_post",
    "account.move": "buat dan ubah hanya draft in_invoice buatan agent",
    "account.move.line": "buat dan ubah hanya di draft itu",
}


def manifest_version(path=AGENT_MANIFEST) -> str:
    """The addon version written in the repository (installed one must be the same)."""
    return ast.literal_eval(path.read_text(encoding="utf-8"))["version"]


def judge_operations(allowed: Iterable[str], expected: Iterable[str]) -> tuple[str, bool]:
    """Describe the allowed operations and say whether they are exactly the expected ones."""
    allowed, expected = set(allowed), set(expected)
    yes = [OPERATION_LABELS[op] for op in OPERATIONS if op in allowed]
    no = [OPERATION_LABELS[op] for op in OPERATIONS if op not in allowed]
    text = ", ".join(yes) or "-"
    if no:
        text += "; tidak: " + ", ".join(no)
    return text, allowed == expected


class Checker:
    def __init__(
        self,
        admin: OdooClient,
        agent: OdooClient | None,
        ds: Dataset,
        *,
        env_names: Callable[[], dict[str, list[str]]] = container_env_names,
        conf_option: Callable[[str], str | None] = odoo_conf_option,
    ) -> None:
        self.c = admin
        self.agent = agent
        self.ds = ds
        self.x = XmlIds(admin)
        self.rows: list[tuple[str, str, str]] = []
        self.env_names = env_names
        self.conf_option = conf_option

    # -- helpers ------------------------------------------------------------------------------

    def row(self, check: str, result: str, ok: bool | None) -> None:
        status = INFO if ok is None else (OK if ok else FAIL)
        self.rows.append((check, result, status))

    def seeded(self, model: str, prefix: str) -> dict[str, int]:
        """Dataset key -> record id, from the fb_seed external IDs of ``model``."""
        return {
            name[len(prefix) :]: res_id
            for name, (m, res_id) in self.x.names().items()
            if m == model and name.startswith(prefix)
        }

    @property
    def failed(self) -> bool:
        return any(status == FAIL for _, _, status in self.rows)

    # -- master data ----------------------------------------------------------------------------

    def check_company(self) -> None:
        company = self.c.search_read("res.company", [], ["name", "vat", "currency_id"], limit=1)[0]
        c = self.ds.company
        ok = company["name"] == c.name and company["vat"] == c.npwp
        ok = ok and company["currency_id"][1] == c.currency
        self.row("Perusahaan", f"{company['name']}, NPWP {company['vat']}", ok)

        tax_id = self.x.ref(TAX_XMLID, "account.tax")
        tax = self.c.read(
            "account.tax", [tax_id], ["name", "amount", "amount_type", "type_tax_use"]
        )[0]
        rate = self.ds.purchase_tax.effective_rate_percent
        ok = (tax["amount"], tax["amount_type"], tax["type_tax_use"]) == (
            rate,
            "percent",
            "purchase",
        )
        self.row("Pajak pembelian", f"{tax['name']}: {tax['amount']:g}% atas harga", ok)

    def check_banks(self) -> None:
        banks = self.seeded("res.bank", "")
        names = {
            b["id"]: b["name"] for b in self.c.read("res.bank", list(banks.values()), ["name"])
        }
        want = {b.key: b.name for b in self.ds.banks}
        ok = {k: names.get(i) for k, i in banks.items()} == want
        self.row("Bank", f"{len(banks)} bank seed (dataset {len(want)})", ok)

    def check_vendors(self) -> None:
        vendors = self.seeded("res.partner", "vendor_")
        rows = self.c.read(
            "res.partner", list(vendors.values()), ["name", "vat", "email", "supplier_rank"]
        )
        by_id = {r["id"]: r for r in rows}
        bad = [
            v.key
            for v in self.ds.vendors
            if (rec := by_id.get(vendors.get(v.key))) is None
            or (rec["name"], rec["vat"], rec["email"]) != (v.name, v.npwp, v.billing_email)
            or rec["supplier_rank"] < 1
        ]
        self.row(
            "Vendor",
            f"{len(vendors)} vendor (dataset {len(self.ds.vendors)})"
            + (f"; beda: {bad}" if bad else ""),
            len(vendors) == len(self.ds.vendors) and not bad,
        )
        for x in self.ds.external_vendors:
            found = self.c.search_count(
                "res.partner", ["|", ["name", "=ilike", x.name], ["vat", "=", x.npwp]]
            )
            self.row(f"{x.name} ({x.key}) tidak ada di Odoo", f"{found} record", found == 0)

        accounts = self.seeded("res.partner.bank", "bank_account_")
        rows = self.c.read(
            "res.partner.bank", list(accounts.values()), ["acc_number", "partner_id", "bank_id"]
        )
        by_id = {r["id"]: r for r in rows}
        banks = self.seeded("res.bank", "")
        bad = []
        for v in self.ds.vendors:
            rec = by_id.get(accounts.get(v.key))
            want = (v.bank_account.number, vendors.get(v.key), banks.get(v.bank_account.bank))
            if (
                rec is None
                or (rec["acc_number"], m2o_id(rec["partner_id"]), m2o_id(rec["bank_id"])) != want
            ):
                bad.append(v.key)
        self.row(
            "Rekening vendor",
            f"{len(accounts)} rekening" + (f"; beda: {bad}" if bad else ""),
            len(accounts) == len(self.ds.vendors) and not bad,
        )

    def check_products(self) -> None:
        tax_id = self.x.ref(TAX_XMLID, "account.tax")
        templates = self.seeded("product.template", "product_template_")
        rows = self.c.read(
            "product.template",
            list(templates.values()),
            ["default_code", "uom_id", "purchase_method", "is_storable", "supplier_taxes_id"],
        )
        by_id = {r["id"]: r for r in rows}
        sellers = self.seeded("product.supplierinfo", "supplierinfo_")
        seller_rows = {
            r["id"]: r
            for r in self.c.read(
                "product.supplierinfo",
                list(sellers.values()),
                ["product_code", "price", "partner_id"],
            )
        }
        vendors = self.seeded("res.partner", "vendor_")
        bad = []
        for p in self.ds.products:
            rec = by_id.get(templates.get(p.key))
            seller = seller_rows.get(sellers.get(p.key))
            if (
                rec is None
                or rec["default_code"] != p.code
                or rec["uom_id"][1] != p.uom
                or rec["purchase_method"] != "receive"
                or not rec["is_storable"]
                or rec["supplier_taxes_id"] != [tax_id]
                or seller is None
                or seller["product_code"] != p.vendor_code
                or round(seller["price"]) != p.purchase_price
                or m2o_id(seller["partner_id"]) != vendors.get(p.vendor)
            ):
                bad.append(p.key)
        self.row(
            "Produk (satuan, kontrol tagihan diterima, pajak, info pemasok)",
            f"{len(templates)} produk" + (f"; beda: {bad}" if bad else ""),
            len(templates) == len(self.ds.products) and not bad,
        )

    # -- purchase orders ------------------------------------------------------------------------

    def load_pos(self) -> None:
        self.po_ids = self.seeded("purchase.order", "po_")
        self.pos = {
            r["id"]: r
            for r in self.c.read(
                "purchase.order",
                list(self.po_ids.values()),
                ["name", "state", "partner_id", "receipt_status", "picking_ids", "amount_total"],
            )
        }
        lines = self.c.search_read(
            "purchase.order.line",
            [["order_id", "in", list(self.po_ids.values())], ["display_type", "=", False]],
            [
                "order_id",
                "product_id",
                "product_qty",
                "price_unit",
                "qty_received",
                "qty_invoiced",
                "tax_ids",
            ],
        )
        self.lines: dict[int, dict[int, dict[str, Any]]] = {}
        for line in lines:
            self.lines.setdefault(m2o_id(line["order_id"]), {})[m2o_id(line["product_id"])] = line
        self.products = self.seeded("product.product", "product_")

    def check_purchase_orders(self) -> None:
        ds_pos = self.ds.purchase_orders
        want_states = Counter(PO_STATE[po.status] for po in ds_pos)
        have_states = Counter(self.pos[i]["state"] for i in self.po_ids.values())
        names_ok = all(self.pos[self.po_ids[po.key]]["name"] == po.number for po in ds_pos)
        self.row(
            "Purchase order",
            f"{len(self.po_ids)} PO: {have_states['purchase']} terkonfirmasi, "
            f"{have_states['cancel']} batal, {have_states['draft']} RFQ",
            len(self.po_ids) == len(ds_pos) and have_states == want_states and names_ok,
        )
        n_lines = sum(len(v) for v in self.lines.values())
        want_lines = sum(len(po.lines) for po in ds_pos)
        self.row("Baris PO", f"{n_lines} baris (dataset {want_lines})", n_lines == want_lines)

        totals_bad = [
            po.number
            for po in ds_pos
            if round(self.pos[self.po_ids[po.key]]["amount_total"]) != po.total()
        ]
        self.row(
            "Total PO (subtotal + PPN 11% efektif)",
            "semua sama dengan dataset" if not totals_bad else f"beda: {totals_bad}",
            not totals_bad,
        )

        tax_id = self.x.ref(TAX_XMLID, "account.tax")
        bad = []
        for po in ds_pos:
            lines = self.lines.get(self.po_ids[po.key], {})
            for line in po.lines:
                rec = lines.get(self.products.get(line.product))
                want = (
                    float(line.qty),
                    float(line.unit_price),
                    float(po.received_qty(line.product)),
                    float(po.billed_qty(line.product)),
                )
                have = (
                    (
                        rec["product_qty"],
                        rec["price_unit"],
                        rec["qty_received"],
                        rec["qty_invoiced"],
                    )
                    if rec
                    else None
                )
                if have is None or any(abs(a - b) > EPS for a, b in zip(have, want, strict=True)):
                    bad.append(f"{po.number}/{line.product}")
                elif rec["tax_ids"] != [tax_id]:
                    bad.append(f"{po.number}/{line.product} (pajak)")
        self.row(
            "Per baris PO: harga, qty, qty diterima, qty ditagih",
            f"{want_lines - len(bad)}/{want_lines} sesuai" + (f"; beda: {bad[:5]}" if bad else ""),
            not bad,
        )

    def check_receipts(self) -> None:
        confirmed = [po for po in self.ds.purchase_orders if po.is_confirmed]
        want = Counter(
            "full" if po.is_fully_received else "partial" if po.is_partially_received else "pending"
            for po in confirmed
        )
        have = Counter(self.pos[self.po_ids[po.key]]["receipt_status"] for po in confirmed)
        self.row(
            "Penerimaan per PO terkonfirmasi",
            f"{have['full']} penuh, {have['partial']} sebagian, {have['pending']} belum diterima",
            have == want,
        )
        picking_ids = [p for i in self.po_ids.values() for p in self.pos[i]["picking_ids"]]
        pickings = self.c.read("stock.picking", picking_ids, ["state", "backorder_id", "date_done"])
        open_backorders = [
            p for p in pickings if p["backorder_id"] and p["state"] not in ("done", "cancel")
        ]
        want_backorders = sum(
            1 for po in confirmed for r in po.receipts if r.remainder == "backorder"
        )
        self.row(
            "Backorder terbuka",
            f"{len(open_backorders)} (dataset {want_backorders})",
            len(open_backorders) == want_backorders,
        )
        done = [p for p in pickings if p["state"] == "done"]
        want_done = sum(len(po.receipts) for po in confirmed)
        self.row(
            "Penerimaan selesai (tanggal dari dataset)",
            f"{len(done)} (dataset {want_done})",
            len(done) == want_done,
        )

    def check_bills(self) -> None:
        bills = self.seeded("account.move", "bill_")
        rows = {
            r["id"]: r
            for r in self.c.read(
                "account.move",
                list(bills.values()),
                ["state", "ref", "invoice_date", "partner_id", "move_type", "amount_total"],
            )
        }
        vendors = self.seeded("res.partner", "vendor_")
        bad = []
        for po, bill in self.ds.history_bills:
            rec = rows.get(bills.get(po.key))
            if (
                rec is None
                or rec["state"] != "posted"
                or rec["move_type"] != "in_invoice"
                or rec["ref"] != bill.vendor_invoice_number
                or rec["invoice_date"] != str(bill.invoice_date)
                or m2o_id(rec["partner_id"]) != vendors.get(po.vendor)
                or round(rec["amount_total"]) != po.total()
            ):
                bad.append(po.number)
        posted_all = self.c.search_count(
            "account.move", [["move_type", "=", "in_invoice"], ["state", "=", "posted"]]
        )
        self.row(
            "Tagihan vendor terposting (ref, tanggal, vendor, total)",
            f"{posted_all} terposting; {len(bills)} seed" + (f"; beda: {bad}" if bad else ""),
            posted_all == len(self.ds.history_bills) == len(bills) and not bad,
        )
        for vendor in self.ds.vendors:
            if any(po.bills for po in self.ds.pos_of_vendor(vendor.key)):
                continue
            n = self.c.search_count(
                "account.move",
                [
                    ["move_type", "=", "in_invoice"],
                    ["state", "=", "posted"],
                    ["partner_id", "=", vendors.get(vendor.key)],
                ],
            )
            self.row(f"{vendor.key} tanpa tagihan terposting", f"{n} tagihan", n == 0)

    def check_numbers(self) -> None:
        n = self.c.search_count("purchase.order", [["name", "=", "P00330"]])
        self.row("P00330 tidak ada", f"{n} PO", n == 0)
        seq = self.c.search_read(
            "ir.sequence",
            [["code", "=", "purchase.order"]],
            ["prefix", "padding", "number_next_actual"],
        )
        if len(seq) != 1:
            self.row("Sequence PO berikutnya", f"{len(seq)} sequence", False)
            return
        s = seq[0]
        number = f"{s['prefix'] or ''}{s['number_next_actual']:0{s['padding']}d}"
        self.row(
            f"Sequence PO berikutnya ≥ P{MIN_NEXT_PO_NUMBER:05d}",
            number,
            s["number_next_actual"] >= MIN_NEXT_PO_NUMBER,
        )

    # -- scenario data needs (SPEC §15.3) -------------------------------------------------------

    def _po_signature(self, number: str) -> tuple:
        po = self.ds.po_by_number(number)
        rec = self.pos[self.po_ids[po.key]]
        lines = self.lines.get(rec["id"], {})
        return (
            m2o_id(rec["partner_id"]),
            rec["state"],
            tuple(
                sorted(
                    (pid, line["product_qty"], line["price_unit"], line["qty_received"])
                    for pid, line in lines.items()
                )
            ),
        )

    def check_scenario_needs(self) -> None:
        a, b = self._po_signature("P00035"), self._po_signature("P00036")
        self.row(
            "P00035 dan P00036 identik",
            "vendor, baris, dan penerimaan sama" if a == b else "berbeda",
            a == b,
        )

        cancelled, replacement = self._po_signature("P00013"), self._po_signature("P00016")
        same_lines = [x[:3] for x in cancelled[2]] == [x[:3] for x in replacement[2]]
        ok = cancelled[1] == "cancel" and replacement[1] == "purchase" and same_lines
        self.row(
            "P00013 batal, P00016 terbuka dengan baris sama",
            f"P00013 {cancelled[1]}, P00016 {replacement[1]}",
            ok,
        )

        candidates = self.strong_candidates("v03", [("p07", 240, 32_000)])
        self.row(
            "Kandidat kuat 240 kg p07 di PO terbuka v03 (BR-06)",
            ", ".join(candidates) or "tidak ada",
            candidates == ["P00033"],
        )

    def strong_candidates(
        self, vendor_key: str, invoice_lines: list[tuple[str, int, int]]
    ) -> list[str]:
        """BR-06 from Odoo data: open qty (received - billed) equal, price within tolerance."""
        vendor_id = self.seeded("res.partner", "vendor_")[vendor_key]
        found = []
        for po in self.ds.purchase_orders:
            rec = self.pos[self.po_ids[po.key]]
            if m2o_id(rec["partner_id"]) != vendor_id or rec["state"] != "purchase":
                continue
            lines = self.lines.get(rec["id"], {})
            ok = True
            for product, qty, price in invoice_lines:
                line = lines.get(self.products[product])
                if line is None:
                    ok = False
                    break
                open_qty = line["qty_received"] - line["qty_invoiced"]
                within = price * 100 <= line["price_unit"] * (100 + PRICE_TOLERANCE_PERCENT)
                if abs(open_qty - qty) > EPS or not within:
                    ok = False
                    break
            if ok:
                found.append(rec["name"])
        return sorted(found)

    # -- label leakage --------------------------------------------------------------------------

    def _scan(self, model: str, ids: Iterable[int]) -> list[str]:
        ids = sorted(set(ids))
        if not ids:
            return []
        available = self.c.fields_get(model, ["type"])
        fields = [f for f in LEAK_FIELDS[model] if f in available]
        hits = []
        for rec in self.c.read(model, ids, fields):
            for field in fields:
                value = rec.get(field)
                if isinstance(value, str) and (m := LABEL_PATTERN.search(value)):
                    hits.append(f"{model}#{rec['id']}.{field}: {m.group(0)}")
        return hits

    def check_label_leak(self) -> None:
        names = self.x.names()
        by_model: dict[str, list[int]] = {}
        for _, (model, res_id) in names.items():
            by_model.setdefault(model, []).append(res_id)
        hits = [f"ir.model.data fb_seed.{n}" for n in names if LABEL_PATTERN.search(n)]

        po_ids = by_model.get("purchase.order", [])
        bill_ids = by_model.get("account.move", [])
        picking_ids = [p for i in po_ids for p in self.pos[i]["picking_ids"]]
        po_line_ids = [line["id"] for lines in self.lines.values() for line in lines.values()]
        bill_line_ids = self.c.search("account.move.line", [["move_id", "in", bill_ids]])
        message_ids = self.c.search(
            "mail.message",
            [
                "|",
                "|",
                "&",
                ["model", "=", "purchase.order"],
                ["res_id", "in", po_ids],
                "&",
                ["model", "=", "account.move"],
                ["res_id", "in", bill_ids],
                "&",
                ["model", "=", "stock.picking"],
                ["res_id", "in", picking_ids],
            ],
        )
        company_ids = self.c.search("res.company", [])
        targets = {
            **{m: by_model.get(m, []) for m in LEAK_FIELDS if m in by_model},
            "res.company": company_ids,
            "stock.picking": picking_ids,
            "purchase.order.line": po_line_ids,
            "account.move.line": bill_line_ids,
            "mail.message": message_ids,
        }
        scanned = 0
        for model, ids in targets.items():
            if model not in LEAK_FIELDS:
                continue
            scanned += len(set(ids))
            hits += self._scan(model, ids)
        self.row(
            "Kebocoran label (pola ID skenario di record seed)",
            f"{scanned} record diperiksa, {len(hits)} temuan" + (f": {hits[:3]}" if hits else ""),
            not hits,
        )

    # -- agent setup and rights (M2.1) -----------------------------------------------------------

    def _xmlids(self, model: str, ids: Iterable[int]) -> list[str]:
        rows = self.c.search_read(
            "ir.model.data",
            [["model", "=", model], ["res_id", "in", sorted(set(ids))]],
            ["module", "name", "res_id"],
        )
        return sorted(f"{r['module']}.{r['name']}" for r in rows)

    def check_agent_setup(self) -> None:
        addon = self.c.search_read(
            "ir.module.module", [["name", "=", AGENT_ADDON]], ["state", "latest_version"]
        )
        want = manifest_version()
        state = addon[0]["state"] if addon else "tidak ada"
        version = addon[0]["latest_version"] if addon else None
        self.row(
            f"Addon {AGENT_ADDON} terpasang (versi repo {want})",
            f"{state}, versi {version}",
            state == "installed" and version == want,
        )

        agent_id = self.x.get("user_agen", "res.users")
        if agent_id is None:
            self.row("User agent", "fb_seed.user_agen tidak ada", False)
            return
        user = self.c.read("res.users", [agent_id], ["group_ids", "all_group_ids"])[0]
        explicit = self._xmlids("res.groups", user["group_ids"])
        want_groups = sorted([*AGENT_GROUPS, INBOX_GROUP])
        self.row(
            "Grup user agent persis grup addon + notifikasi inbox",
            ", ".join(explicit) + f" ({len(user['all_group_ids'])} grup termasuk tersirat)",
            explicit == want_groups,
        )
        implied = set(self._xmlids("res.groups", user["all_group_ids"]))
        bad = sorted(implied & set(FORBIDDEN_AGENT_GROUPS))
        self.row(
            "Agent tanpa grup posting atau aturan semua tagihan (langsung maupun tersirat)",
            "tidak ada" if not bad else ", ".join(bad),
            not bad,
        )

        key_name = access_params(self.ds)["agent"]["key_name"]
        keys = self.c.search_read(
            "res.users.apikeys", [["user_id", "=", agent_id]], ["name", "expiration_date"]
        )
        now = datetime.now()
        limit = now + timedelta(days=API_KEY_DAYS, minutes=5)
        good = [
            k
            for k in keys
            if k["name"] == key_name
            and k["expiration_date"]
            and now < datetime.fromisoformat(k["expiration_date"]) <= limit
        ]
        self.row(
            f"Key agent dibuat odoo:init tanpa sudo, berlaku ≤ {API_KEY_DAYS} hari",
            (
                f"{len(keys)} key; berlaku sampai {good[0]['expiration_date'][:10]}"
                if good
                else f"{len(keys)} key, tidak ada yang bernama dan berdurasi benar"
            ),
            len(keys) == 1 and len(good) == 1,
        )

    def _rule_targets(self) -> list[tuple[str, str, int | None]]:
        """Seed records for the record-rule level: (model, description, id)."""
        vendors = self.seeded("res.partner", "vendor_")
        po_id = self.po_ids["o01"]
        po_line = next(iter(self.lines.get(po_id, {}).values()), None)
        picking = next(iter(self.pos[po_id]["picking_ids"]), None)
        move = self.c.search("stock.move", [["picking_id", "=", picking]], limit=1)
        move_line = self.c.search("stock.move.line", [["move_id", "in", move]], limit=1)
        bill = self.seeded("account.move", "bill_").get("h01")
        bill_line = self.c.search("account.move.line", [["move_id", "=", bill]], limit=1)
        message = self.c.search(
            "mail.message", [["model", "=", "purchase.order"], ["res_id", "=", po_id]], limit=1
        )
        company = self.c.search("res.company", [], limit=1)
        po_name = self.pos[po_id]["name"]
        return [
            ("res.company", "perusahaan", next(iter(company), None)),
            ("res.partner", "vendor v01", vendors.get("v01")),
            ("res.bank", "bank Bank Mandala Raya", self.seeded("res.bank", "").get("bank_mandala")),
            (
                "res.partner.bank",
                "rekening v01",
                self.seeded("res.partner.bank", "bank_account_").get("v01"),
            ),
            ("product.product", "produk p01", self.products.get("p01")),
            (
                "product.template",
                "template p01",
                self.seeded("product.template", "product_template_").get("p01"),
            ),
            (
                "product.supplierinfo",
                "info pemasok p01",
                self.seeded("product.supplierinfo", "supplierinfo_").get("p01"),
            ),
            ("purchase.order", f"PO {po_name}", po_id),
            ("purchase.order.line", f"baris {po_name}", po_line["id"] if po_line else None),
            ("stock.picking", f"penerimaan {po_name}", picking),
            ("stock.move", f"stock.move {po_name}", next(iter(move), None)),
            ("stock.move.line", f"stock.move.line {po_name}", next(iter(move_line), None)),
            ("account.move", "tagihan riwayat terposting", bill),
            ("account.move.line", "baris tagihan riwayat", next(iter(bill_line), None)),
            ("mail.message", f"pesan chatter {po_name}", next(iter(message), None)),
        ]

    def check_agent_rights(self) -> None:
        if self.agent is None:
            self.row("Key agent", "ODOO_AGENT_API_KEY kosong", False)
            return
        unreadable = []
        for model in AGENT_ACL:
            try:
                rows = self.agent.search_read(model, [], ["id"], limit=1)
            except OdooError as exc:
                unreadable.append(f"{model} ({type(exc).__name__})")
                continue
            if not rows and model not in ("ir.attachment",):
                unreadable.append(f"{model} (kosong)")
        self.row(
            "Key agent bisa membaca semua model di matriks (JSON-2)",
            (
                f"{len(AGENT_ACL)}/{len(AGENT_ACL)} model terbaca"
                if not unreadable
                else f"gagal: {unreadable}"
            ),
            not unreadable,
        )

        for model, expected in AGENT_ACL.items():
            allowed = [
                op
                for op in OPERATIONS
                if self.agent.call(model, "has_access", ids=[], operation=op)
            ]
            text, ok = judge_operations(allowed, expected)
            if model in AGENT_RULE_CLOSED:
                text += f" ({AGENT_RULE_CLOSED[model]})"
            self.row(f"Hak agent tingkat ACL: {model}", text, ok)

        for model, what, record_id in self._rule_targets():
            if record_id is None:
                self.row(f"Hak agent tingkat aturan: {what}", "record seed tidak ada", False)
                continue
            allowed = [
                op
                for op in OPERATIONS
                if self.agent.call(model, "has_access", ids=[record_id], operation=op)
            ]
            text, ok = judge_operations(allowed, READ_ONLY)
            self.row(f"Hak agent tingkat aturan: {what} ({model})", text, ok)

    # -- local runtime (ADR-0003, tinjauan M0) -------------------------------------------------

    def check_runtime(self) -> None:
        cron = self.conf_option("max_cron_threads")
        self.row(
            "Cron Odoo mati (infra/odoo/odoo.conf)",
            f"max_cron_threads = {cron}" if cron is not None else "max_cron_threads tidak diatur",
            cron is not None and cron.strip() == "0",
        )
        title = "Env kontainer tanpa rahasia aplikasi (nama saja)"
        try:
            names = self.env_names()
        except (RuntimeError, OSError) as exc:
            self.row(title, f"tidak bisa membaca env kontainer: {exc}", False)
            return
        bad = forbidden_env(names)
        if bad:
            result = "; ".join(f"{c}: {', '.join(v)}" for c, v in bad.items())
        elif not names:
            result = "tidak ada kontainer fakturberes yang berjalan"
        else:
            result = f"{len(names)} kontainer, tanpa {len(FORBIDDEN_CONTAINER_ENV)} nama terlarang"
        self.row(title, result, bool(names) and not bad)
        for container, env in names.items():
            self.row(f"Nama env {container}", ", ".join(env), None)

    # -- run ------------------------------------------------------------------------------------

    def run(self) -> None:
        self.check_company()
        self.check_banks()
        self.check_vendors()
        self.check_products()
        self.load_pos()
        self.check_purchase_orders()
        self.check_receipts()
        self.check_bills()
        self.check_numbers()
        self.check_scenario_needs()
        self.check_label_leak()
        self.check_agent_setup()
        self.check_agent_rights()
        self.check_runtime()


def main() -> int:
    ds = get_dataset()
    settings = Settings()
    try:
        with admin_client(settings) as admin:
            agent = None
            if settings.odoo_agent_api_key is not None:
                agent = OdooClient(
                    settings.odoo_url,
                    settings.odoo_db,
                    settings.odoo_agent_api_key,
                    user_agent="faktur-beres-check/0.1",
                )
            try:
                checker = Checker(admin, agent, ds)
                checker.run()
            finally:
                if agent is not None:
                    agent.close()
    except (SeedError, OdooError) as exc:
        print(f"galat: {exc}", file=sys.stderr)
        return 2
    print_table(("Pemeriksaan", "Hasil", "Status"), checker.rows)
    failed = [r for r in checker.rows if r[2] == FAIL]
    print()
    if failed:
        print(f"odoo:check GAGAL: {len(failed)} dari {len(checker.rows)} pemeriksaan")
        return 1
    print(f"odoo:check lulus: {len(checker.rows)} pemeriksaan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
