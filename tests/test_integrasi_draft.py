"""Integration: three draft bills through odoo_tools, compared with the labels, and idempotency.

COC-01 (P00015), HRG-01 (P00022, price 185.900), and QTY-03 (P00029, 840 of 1.050 kg): the plan
comes from ``analyse()`` with ``OdooErpReader`` (agent key), then ``create_draft_bill``,
``attach_file`` with the original file from ``eval/berkas/``, and ``post_note``. Odoo is reset
before and after by ``npm run test:integrasi``.
"""

import time
from collections import Counter
from decimal import Decimal

import pytest

from faktur_beres.dataset import get_dataset
from faktur_beres.domain.normalize import normalize_invoice_number, tax_id_digits
from faktur_beres.domain.reasons import Route
from faktur_beres.evals.labels import load_labels, truth_to_invoice
from faktur_beres.tools import odoo_tools
from faktur_beres.tools.odoo_gateway import AgentOdooGateway
from faktur_beres.tools.odoo_tools import OdooErpReader
from integrasi_support import POLICY, analyse_label, keys, original_file

pytestmark = pytest.mark.integrasi

#: Expected bills (from the labels and the task): PO, total, and the price or quantity to watch.
CASES = {
    "COC-01": ("P00015", Decimal(24975000)),
    "HRG-01": ("P00022", Decimal(12380940)),
    "QTY-03": ("P00029", Decimal(38228400)),
}


def po_lines(gw: AgentOdooGateway, ids: list[int]) -> dict[int, dict]:
    rows = gw.read("purchase.order.line", ids, ["id", "order_id", "product_id", "qty_invoiced"])
    return {r["id"]: r for r in rows}


@pytest.fixture(scope="module")
def made(agent_gateway: AgentOdooGateway, odoo_erp: OdooErpReader) -> dict[str, dict]:
    labels = load_labels()
    result = {}
    for sid in CASES:
        label = labels[sid]
        analysis = analyse_label(label, odoo_erp)
        assert analysis.default_route == Route.A, sid
        plan = analysis.bill_plan
        assert plan is not None, sid
        line_ids = [ln.purchase_line_id for ln in plan.lines]
        before = po_lines(agent_gateway, line_ids)
        k = keys(label, f"vendor:{plan.partner_id}")
        started = time.monotonic()
        bill = odoo_tools.create_draft_bill(
            agent_gateway, plan, k["create_draft_bill"], tolerance=POLICY.rounding.total
        )
        assert bill.bill_id is not None, (sid, bill)
        name, data, mimetype = original_file(label)
        attachment = odoo_tools.attach_file(agent_gateway, bill.bill_id, name, data, mimetype)
        invoice = truth_to_invoice(label.kebenaran_dokumen)
        text = odoo_tools.bill_note_text(
            invoice.invoice_number, invoice.nsfp, invoice.tax_invoice_date, name
        )
        note = odoo_tools.post_note(agent_gateway, bill.bill_id, text, k["post_note"])
        seconds = time.monotonic() - started
        print(
            f"\n  draft | {sid} | #{bill.bill_id} {bill.outcome} "
            f"terverifikasi={bill.verified} | {seconds:.2f} detik"
        )
        result[sid] = {
            "label": label,
            "plan": plan,
            "keys": k,
            "bill": bill,
            "attachment": attachment,
            "note": note,
            "file": (name, data, mimetype),
            "text": text,
            "before": before,
        }
    return result


def test_drafts_are_created_and_verified(made) -> None:
    for sid, m in made.items():
        assert (m["bill"].outcome, m["bill"].verified, m["bill"].differences) == (
            "created",
            True,
            (),
        ), sid
        assert m["attachment"].created and m["note"].created, sid


def test_drafts_match_the_labels(made, agent_gateway: AgentOdooGateway) -> None:
    ds = get_dataset()
    vendor_keys = {tax_id_digits(v.npwp): v.key for v in ds.vendors}
    product_keys = {p.code: p.key for p in ds.products}
    for sid, m in made.items():
        expected = m["label"].harapan.tagihan_odoo
        (bill,) = odoo_tools.bills_by_ids(agent_gateway, [m["bill"].bill_id])
        (partner,) = agent_gateway.read("res.partner", [bill.vendor_id], ["id", "vat"])
        lines = po_lines(agent_gateway, [ln.purchase_line_id for ln in bill.lines])
        products = {
            p["id"]: p["default_code"]
            for p in agent_gateway.read(
                "product.product",
                [ln["product_id"][0] for ln in lines.values()],
                ["id", "default_code"],
            )
        }
        have = Counter(
            (
                f"{lines[ln.purchase_line_id]['order_id'][1]}/"
                f"{product_keys[products[lines[ln.purchase_line_id]['product_id'][0]]]}",
                ln.quantity,
                ln.price_unit,
            )
            for ln in bill.lines
        )
        want = Counter((b.po_line, Decimal(b.qty), Decimal(b.harga_satuan)) for b in expected.baris)
        po, total = CASES[sid]
        print(
            f"\n  banding | {sid} | vendor {vendor_keys[tax_id_digits(partner['vat'])]} | "
            f"ref {bill.ref} | total {bill.amount_total} | baris {sorted(map(str, have))}"
        )
        assert bill.state.value == expected.status == "draft", sid
        assert vendor_keys[tax_id_digits(partner["vat"])] == expected.vendor, sid
        assert normalize_invoice_number(bill.ref or "") == normalize_invoice_number(expected.ref), (
            sid
        )
        assert abs(bill.amount_total - expected.total) <= 1 and expected.total == total, sid
        assert have == want, sid
        assert all(key[0].startswith(po + "/") for key in have), sid


def test_hrg01_price_and_qty03_quantity(made) -> None:
    hrg = made["HRG-01"]["plan"].lines[0]
    qty = made["QTY-03"]["plan"].lines[0]
    assert hrg.price_unit == 185900
    assert qty.quantity == 840


def test_po_lines_are_linked_and_invoiced(made, agent_gateway: AgentOdooGateway) -> None:
    for sid, m in made.items():
        (bill,) = odoo_tools.bills_by_ids(agent_gateway, [m["bill"].bill_id])
        assert bill.lines and all(ln.purchase_line_id for ln in bill.lines), sid
        after = po_lines(agent_gateway, [ln.purchase_line_id for ln in bill.lines])
        for ln in bill.lines:
            gained = (
                after[ln.purchase_line_id]["qty_invoiced"]
                - m["before"][ln.purchase_line_id]["qty_invoiced"]
            )
            assert gained == ln.quantity, sid


def test_attachment_and_note_are_on_the_draft(made, agent_gateway: AgentOdooGateway) -> None:
    for sid, m in made.items():
        bill_id = m["bill"].bill_id
        (att,) = agent_gateway.read(
            "ir.attachment",
            [m["attachment"].attachment_id],
            ["id", "res_model", "res_id", "checksum", "name"],
        )
        assert (att["res_model"], att["res_id"], att["checksum"]) == (
            "account.move",
            bill_id,
            m["attachment"].checksum,
        ), sid
        assert att["name"] == m["file"][0]
        (msg,) = agent_gateway.read(
            "mail.message", [m["note"].message_id], ["id", "body", "subtype_id", "res_id"]
        )
        assert msg["res_id"] == bill_id and m["note"].marker in msg["body"], sid
        assert "NSFP" in msg["body"]
        assert msg["subtype_id"] and msg["subtype_id"][1] in ("Note", "Catatan"), sid


def test_vendor_bills_have_no_nsfp_field(agent_gateway: AgentOdooGateway) -> None:
    assert odoo_tools.vendor_bill_nsfp_field(agent_gateway) is None


def test_repeating_every_action_creates_nothing_new(made, agent_gateway: AgentOdooGateway) -> None:
    for sid, m in made.items():
        bill_id = m["bill"].bill_id
        again = odoo_tools.create_draft_bill(
            agent_gateway, m["plan"], m["keys"]["create_draft_bill"]
        )
        name, data, mimetype = m["file"]
        attachment = odoo_tools.attach_file(agent_gateway, bill_id, name, data, mimetype)
        note = odoo_tools.post_note(agent_gateway, bill_id, m["text"], m["keys"]["post_note"])
        print(
            f"\n  idempotensi | {sid} | draft {again.outcome} #{again.bill_id} | "
            f"lampiran dibuat={attachment.created} | catatan dibuat={note.created}"
        )
        assert (again.outcome, again.bill_id, again.verified) == ("reused", bill_id, True), sid
        assert (attachment.created, attachment.attachment_id) == (
            False,
            m["attachment"].attachment_id,
        )
        assert (note.created, note.message_id) == (False, m["note"].message_id)
        ref = normalize_invoice_number(m["plan"].ref)
        same_key = [
            b
            for b in odoo_tools.vendor_bills(agent_gateway, m["plan"].partner_id)
            if normalize_invoice_number(b.ref or "") == ref
        ]
        assert [b.id for b in same_key] == [bill_id], sid
        assert (
            agent_gateway.search_count(
                "ir.attachment", [["res_model", "=", "account.move"], ["res_id", "=", bill_id]]
            )
            == 1
        ), sid
        assert (
            agent_gateway.search_count(
                "mail.message",
                [
                    ["model", "=", "account.move"],
                    ["res_id", "=", bill_id],
                    ["body", "ilike", m["note"].marker],
                ],
            )
            == 1
        ), sid
