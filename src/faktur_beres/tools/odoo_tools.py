"""``odoo_tools`` (SPEC §5.2): typed reads and the three writes, all through ``AgentOdooGateway``.

Inputs and outputs are Pydantic models so that M4 can expose the functions as LangGraph tools. The
reads return the ERP records of ``faktur_beres.domain.erp``; ``OdooErpReader`` implements
``ErpReader`` on top of them for the rules. Searches are loose on the Odoo side (``ilike``) and
exact on ours: every result is filtered again with the normalisation of the business rules.

The writes (``create_draft_bill``, ``attach_file``, ``post_note``) are idempotent and verified;
see their docstrings.
"""

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import (
    BankAccountRecord,
    BankRecord,
    BillLineRecord,
    BillRecord,
    BillState,
    CompanyRecord,
    PoState,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.domain.normalize import (
    account_digits,
    normalize_email,
    normalize_invoice_number,
    normalize_name,
    normalize_po_number,
    tax_id_digits,
)
from faktur_beres.domain.rules.bill_plan import BillPlan
from faktur_beres.tools.errors import ForbiddenOperation, VerificationFailed
from faktur_beres.tools.odoo_gateway import AgentOdooGateway

VENDOR_DOMAIN: list[Any] = [["supplier_rank", ">", 0]]
VENDOR_FIELDS = ("id", "name", "vat", "email")
BANK_FIELDS = ("id", "partner_id", "acc_number", "bank_id", "acc_holder_name")
PO_FIELDS = (
    "id",
    "name",
    "partner_id",
    "state",
    "locked",
    "currency_id",
    "fiscal_position_id",
    "payment_term_id",
    "order_line",
)
PO_LINE_FIELDS = (
    "id",
    "order_id",
    "product_id",
    "name",
    "product_qty",
    "qty_received",
    "qty_invoiced",
    "price_unit",
    "product_uom_id",
    "tax_ids",
    "display_type",
)
BILL_FIELDS = ("id", "partner_id", "ref", "invoice_date", "state", "amount_total", "create_uid")
BILL_LINE_FIELDS = ("id", "move_id", "purchase_line_id", "product_id", "quantity", "price_unit")
COUNTED_BILL_STATES = ["draft", "posted"]


# -- conversions ------------------------------------------------------------------------------


def _m2o(value: Any) -> tuple[int, str] | None:
    """Odoo many2one ``[id, name]`` (or ``False``)."""
    if isinstance(value, list | tuple) and value:
        return int(value[0]), str(value[1]) if len(value) > 1 else ""
    return None


def _m2o_id(value: Any) -> int | None:
    pair = _m2o(value)
    return pair[0] if pair else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    raise TypeError(f"angka Odoo tidak terduga: {value!r}")


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def _vendor(row: dict[str, Any]) -> VendorRecord:
    return VendorRecord(
        id=row["id"], name=row["name"], vat=_text(row.get("vat")), email=_text(row.get("email"))
    )


def _bank(row: dict[str, Any]) -> BankAccountRecord:
    bank = _m2o(row.get("bank_id"))
    partner = _m2o_id(row.get("partner_id"))
    if partner is None:
        raise ValueError(f"rekening #{row['id']} tanpa pemilik")
    return BankAccountRecord(
        id=row["id"],
        partner_id=partner,
        number=row["acc_number"],
        bank_name=bank[1] if bank else None,
        holder=_text(row.get("acc_holder_name")),
    )


def _unique(records: Iterable[VendorRecord]) -> tuple[VendorRecord, ...]:
    seen: dict[int, VendorRecord] = {}
    for record in records:
        seen.setdefault(record.id, record)
    return tuple(seen.values())


# -- company, vendors, bank accounts -----------------------------------------------------------


def company_profile(gw: AgentOdooGateway) -> CompanyRecord:
    """The company, with its NPWP (``vat``) for BR-10."""
    (row,) = gw.search_read("res.company", [], ["id", "name", "vat"], limit=1)
    return CompanyRecord(id=row["id"], name=row["name"], vat=_text(row.get("vat")))


class VendorSearch(DomainModel):
    npwp: str | None = None
    name: str | None = None
    email: str | None = None


def find_vendors(gw: AgentOdooGateway, query: VendorSearch) -> tuple[VendorRecord, ...]:
    """Vendors by NPWP (as digits), name (BR-05 normalisation), or e-mail (normalised)."""
    found: list[VendorRecord] = []
    digits = tax_id_digits(query.npwp) if query.npwp else None
    if digits:
        rows = gw.search_read(
            "res.partner", [*VENDOR_DOMAIN, ["vat", "ilike", digits[-8:]]], VENDOR_FIELDS
        )
        found += [v for v in map(_vendor, rows) if tax_id_digits(v.vat) == digits]
    wanted_name = normalize_name(query.name) if query.name else ""
    if wanted_name:
        token = max(wanted_name.split(), key=len)
        rows = gw.search_read(
            "res.partner", [*VENDOR_DOMAIN, ["name", "ilike", token]], VENDOR_FIELDS
        )
        found += [v for v in map(_vendor, rows) if normalize_name(v.name) == wanted_name]
    email = normalize_email(query.email) if query.email else None
    if email:
        rows = gw.search_read(
            "res.partner", [*VENDOR_DOMAIN, ["email", "=ilike", email]], VENDOR_FIELDS
        )
        found += [v for v in map(_vendor, rows) if normalize_email(v.email) == email]
    return _unique(found)


def vendor_bank_accounts(gw: AgentOdooGateway, vendor_id: int) -> tuple[BankAccountRecord, ...]:
    rows = gw.search_read("res.partner.bank", [["partner_id", "=", vendor_id]], BANK_FIELDS)
    return tuple(map(_bank, rows))


def registered_banks(gw: AgentOdooGateway) -> tuple[BankRecord, ...]:
    """The banks in ``res.bank`` (BR-12 compares printed bank names with them)."""
    rows = gw.search_read("res.bank", [], ["id", "name"], order="id")
    return tuple(BankRecord(id=r["id"], name=r["name"]) for r in rows if _text(r.get("name")))


def bank_account_owners(gw: AgentOdooGateway, number: str) -> tuple[BankAccountRecord, ...]:
    """Every account in Odoo with these digits, through ``sanitized_acc_number``."""
    digits = account_digits(number)
    if not digits:
        return ()
    rows = gw.search_read("res.partner.bank", [["sanitized_acc_number", "=", digits]], BANK_FIELDS)
    return tuple(a for a in map(_bank, rows) if a.digits == digits)


# -- purchase orders --------------------------------------------------------------------------


def _purchase_orders(
    gw: AgentOdooGateway, rows: Sequence[dict[str, Any]]
) -> tuple[PurchaseOrderRecord, ...]:
    if not rows:
        return ()
    line_ids = sorted({i for row in rows for i in row.get("order_line") or []})
    lines = [
        line
        for line in gw.read("purchase.order.line", line_ids, PO_LINE_FIELDS)
        if not line["display_type"]
    ]
    product_ids = sorted({pid for line in lines if (pid := _m2o_id(line["product_id"]))})
    products = {
        p["id"]: p
        for p in gw.read(
            "product.product", product_ids, ["id", "name", "default_code", "product_tmpl_id"]
        )
    }
    vendor_ids = sorted({vid for row in rows if (vid := _m2o_id(row["partner_id"]))})
    template_ids = sorted(
        {tid for p in products.values() if (tid := _m2o_id(p["product_tmpl_id"]))}
    )
    codes: dict[tuple[int, int], list[str]] = {}
    if vendor_ids and template_ids:
        infos = gw.search_read(
            "product.supplierinfo",
            [["partner_id", "in", vendor_ids], ["product_tmpl_id", "in", template_ids]],
            ["id", "partner_id", "product_tmpl_id", "product_id", "product_code"],
        )
        for info in infos:
            code = _text(info.get("product_code"))
            vendor, template = _m2o_id(info["partner_id"]), _m2o_id(info["product_tmpl_id"])
            if not code or vendor is None or template is None:
                continue
            variant = _m2o_id(info.get("product_id"))
            targets = (
                [variant]
                if variant
                else [
                    pid for pid, p in products.items() if _m2o_id(p["product_tmpl_id"]) == template
                ]
            )
            for pid in targets:
                codes.setdefault((vendor, pid), []).append(code)

    by_order: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        order = _m2o_id(line["order_id"])
        if order is not None:
            by_order.setdefault(order, []).append(line)

    result = []
    for row in rows:
        vendor_id = _m2o_id(row["partner_id"])
        if vendor_id is None:
            continue
        records = []
        for line in sorted(by_order.get(row["id"], []), key=lambda ln: ln["id"]):
            product_id = _m2o_id(line["product_id"])
            uom = _m2o(line["product_uom_id"])
            if product_id is None or uom is None:
                continue
            product = products.get(product_id, {})
            records.append(
                PurchaseOrderLineRecord(
                    id=line["id"],
                    product_id=product_id,
                    product_code=_text(product.get("default_code")),
                    product_name=product.get("name") or line["name"],
                    vendor_codes=tuple(sorted(set(codes.get((vendor_id, product_id), [])))),
                    description=line["name"],
                    uom_id=uom[0],
                    uom_name=uom[1],
                    quantity=_decimal(line["product_qty"]),
                    qty_received=_decimal(line["qty_received"]),
                    qty_invoiced=_decimal(line["qty_invoiced"]),
                    price_unit=_decimal(line["price_unit"]),
                    tax_ids=tuple(line.get("tax_ids") or ()),
                )
            )
        result.append(
            PurchaseOrderRecord(
                id=row["id"],
                number=row["name"],
                vendor_id=vendor_id,
                state=PoState(row["state"]),
                locked=bool(row.get("locked")),
                currency_id=_m2o_id(row.get("currency_id")),
                fiscal_position_id=_m2o_id(row.get("fiscal_position_id")),
                payment_term_id=_m2o_id(row.get("payment_term_id")),
                lines=tuple(records),
            )
        )
    return tuple(result)


def purchase_order_by_number(gw: AgentOdooGateway, number: str) -> PurchaseOrderRecord | None:
    """The PO with exactly this number (BR-06 normalisation), in any state, with its lines."""
    wanted = normalize_po_number(number)
    if not wanted:
        return None
    rows = gw.search_read("purchase.order", [["name", "=ilike", wanted]], PO_FIELDS, limit=2)
    rows = [r for r in rows if normalize_po_number(r["name"]) == wanted]
    if len(rows) != 1:
        return None
    return _purchase_orders(gw, rows)[0]


def confirmed_purchase_orders(
    gw: AgentOdooGateway, vendor_id: int
) -> tuple[PurchaseOrderRecord, ...]:
    """The vendor's confirmed POs (state ``purchase``, locked or not), for BR-06 candidates."""
    rows = gw.search_read(
        "purchase.order",
        [["partner_id", "=", vendor_id], ["state", "=", "purchase"]],
        PO_FIELDS,
        order="name",
    )
    return _purchase_orders(gw, rows)


# -- vendor bills -----------------------------------------------------------------------------


def _bills(gw: AgentOdooGateway, rows: Sequence[dict[str, Any]]) -> tuple[BillRecord, ...]:
    if not rows:
        return ()
    lines = gw.search_read(
        "account.move.line",
        [["move_id", "in", [r["id"] for r in rows]], ["display_type", "=", "product"]],
        BILL_LINE_FIELDS,
    )
    by_move: dict[int, list[BillLineRecord]] = {}
    for line in lines:
        move = _m2o_id(line["move_id"])
        if move is None:
            continue
        by_move.setdefault(move, []).append(
            BillLineRecord(
                id=line["id"],
                purchase_line_id=_m2o_id(line.get("purchase_line_id")),
                product_id=_m2o_id(line.get("product_id")),
                quantity=_decimal(line["quantity"]),
                price_unit=_decimal(line["price_unit"]),
            )
        )
    result = []
    for row in rows:
        vendor = _m2o_id(row["partner_id"])
        if vendor is None:
            continue
        result.append(
            BillRecord(
                id=row["id"],
                vendor_id=vendor,
                ref=_text(row.get("ref")),
                invoice_date=_date(row.get("invoice_date")),
                state=BillState(row["state"]),
                amount_total=_decimal(row["amount_total"]),
                created_by=_m2o_id(row.get("create_uid")),
                lines=tuple(sorted(by_move.get(row["id"], []), key=lambda ln: ln.id)),
            )
        )
    return tuple(result)


def vendor_bills(gw: AgentOdooGateway, vendor_id: int) -> tuple[BillRecord, ...]:
    """The vendor's bills in state draft or posted, with lines linked to PO lines (BR-11)."""
    rows = gw.search_read(
        "account.move",
        [
            ["move_type", "=", "in_invoice"],
            ["partner_id", "=", vendor_id],
            ["state", "in", COUNTED_BILL_STATES],
        ],
        BILL_FIELDS,
        order="id",
    )
    return _bills(gw, rows)


def bills_by_ids(gw: AgentOdooGateway, ids: Sequence[int]) -> tuple[BillRecord, ...]:
    return _bills(gw, gw.read("account.move", ids, BILL_FIELDS))


def posted_bill_count(gw: AgentOdooGateway, vendor_id: int) -> int:
    """Number of posted vendor bills (BR-14: zero means a new vendor)."""
    return gw.search_count(
        "account.move",
        [
            ["move_type", "=", "in_invoice"],
            ["partner_id", "=", vendor_id],
            ["state", "=", "posted"],
        ],
    )


class OdooErpReader:
    """``ErpReader`` over Odoo, with the agent key and through the gateway only."""

    def __init__(self, gw: AgentOdooGateway) -> None:
        self._gw = gw
        self._company: CompanyRecord | None = None
        self._banks: tuple[BankRecord, ...] | None = None

    def company(self) -> CompanyRecord:
        if self._company is None:
            self._company = company_profile(self._gw)
        return self._company

    def vendors_by_vat(self, digits: str) -> Sequence[VendorRecord]:
        return find_vendors(self._gw, VendorSearch(npwp=digits))

    def vendors_by_name(self, name: str) -> Sequence[VendorRecord]:
        return find_vendors(self._gw, VendorSearch(name=name))

    def vendors_by_email(self, email: str) -> Sequence[VendorRecord]:
        return find_vendors(self._gw, VendorSearch(email=email))

    def bank_accounts(self, vendor_id: int) -> Sequence[BankAccountRecord]:
        return vendor_bank_accounts(self._gw, vendor_id)

    def bank_account_owners(self, digits: str) -> Sequence[BankAccountRecord]:
        return bank_account_owners(self._gw, digits)

    def banks(self) -> Sequence[BankRecord]:
        if self._banks is None:
            self._banks = registered_banks(self._gw)
        return self._banks

    def purchase_order(self, number: str) -> PurchaseOrderRecord | None:
        return purchase_order_by_number(self._gw, number)

    def confirmed_purchase_orders(self, vendor_id: int) -> Sequence[PurchaseOrderRecord]:
        return confirmed_purchase_orders(self._gw, vendor_id)

    def vendor_bills(self, vendor_id: int) -> Sequence[BillRecord]:
        return vendor_bills(self._gw, vendor_id)

    def posted_bill_count(self, vendor_id: int) -> int:
        return posted_bill_count(self._gw, vendor_id)


# -- writes -----------------------------------------------------------------------------------


class DraftBillResult(DomainModel):
    """Outcome of ``create_draft_bill`` (BR-18, BR-22)."""

    outcome: Literal["created", "reused", "conflict"]
    bill_id: int | None = None
    verified: bool
    differences: tuple[str, ...] = ()
    total: Decimal | None = None
    conflicts: tuple[int, ...] = ()
    idempotency_key: str


def verify_bill(bill: BillRecord, plan: BillPlan, tolerance: Decimal) -> tuple[str, ...]:
    """What differs between a bill read back from Odoo and its plan (empty when it matches)."""
    differences = []
    if bill.state != BillState.DRAFT:
        differences.append(f"status {bill.state.value}, seharusnya draft")
    if bill.vendor_id != plan.partner_id:
        differences.append(f"vendor #{bill.vendor_id}, seharusnya #{plan.partner_id}")
    if bill.ref != plan.ref:
        differences.append(f"ref {bill.ref!r}, seharusnya {plan.ref!r}")
    if bill.invoice_date != plan.invoice_date:
        differences.append(f"tanggal {bill.invoice_date}, seharusnya {plan.invoice_date}")
    have = Counter((ln.purchase_line_id, ln.quantity, ln.price_unit) for ln in bill.lines)
    want = Counter((ln.purchase_line_id, ln.quantity, ln.price_unit) for ln in plan.lines)
    if have != want:
        differences.append(
            "baris berbeda: tagihan "
            + ", ".join(f"{k[0]}:{k[1]}×{k[2]}" for k in sorted(have, key=str))
            + "; rencana "
            + ", ".join(f"{k[0]}:{k[1]}×{k[2]}" for k in sorted(want, key=str))
        )
    gap = bill.amount_total - plan.expected_total
    if abs(gap) > tolerance:
        differences.append(
            f"total {bill.amount_total} berbeda dari total faktur {plan.expected_total} "
            f"(selisih {gap})"
        )
    return tuple(differences)


def create_draft_bill(
    gw: AgentOdooGateway,
    plan: BillPlan,
    idempotency_key: str,
    *,
    tolerance: Decimal = Decimal(1),
) -> DraftBillResult:
    """Create the one draft vendor bill of ``plan``, idempotently, and verify it.

    1. Natural key: vendor + ``ref`` normalised (BR-11) among the vendor's draft and posted bills.
       A draft the agent created with the same content is reused; anything else is a conflict and
       nothing is created.
    2. Create the bill with all its lines in **one** ``create`` call.
    3. Read it back and verify state draft, vendor, ``ref``, date, every line (PO line, quantity,
       price), and the total within ``tolerance`` of the printed total. A failed verification is
       reported, never repaired here (BR-18: the draft stays as it is and the invoice goes to C).
    """
    wanted = normalize_invoice_number(plan.ref)
    same_key = [
        b
        for b in vendor_bills(gw, plan.partner_id)
        if b.ref and normalize_invoice_number(b.ref) == wanted
    ]
    if same_key:
        if len(same_key) == 1 and same_key[0].state == BillState.DRAFT:
            existing = same_key[0]
            if gw.is_agent_draft(existing.id) and not verify_bill(existing, plan, tolerance):
                return DraftBillResult(
                    outcome="reused",
                    bill_id=existing.id,
                    verified=True,
                    total=existing.amount_total,
                    idempotency_key=idempotency_key,
                )
        return DraftBillResult(
            outcome="conflict",
            verified=False,
            differences=tuple(
                f"sudah ada tagihan #{b.id} ({b.state.value}) untuk vendor dan nomor ini"
                for b in same_key
            ),
            conflicts=tuple(b.id for b in same_key),
            idempotency_key=idempotency_key,
        )

    bill_id = gw.create_vendor_bill(plan.odoo_values())
    created = bills_by_ids(gw, [bill_id])
    if not created:
        return DraftBillResult(
            outcome="created",
            bill_id=bill_id,
            verified=False,
            differences=("tagihan tidak terbaca ulang",),
            idempotency_key=idempotency_key,
        )
    bill = created[0]
    differences = verify_bill(bill, plan, tolerance)
    return DraftBillResult(
        outcome="created",
        bill_id=bill_id,
        verified=not differences,
        differences=differences,
        total=bill.amount_total,
        idempotency_key=idempotency_key,
    )


class AttachmentResult(DomainModel):
    attachment_id: int
    created: bool
    checksum: str  # SHA-1 of the content, as ``ir.attachment.checksum``


def attach_file(
    gw: AgentOdooGateway, bill_id: int, name: str, content: bytes, mimetype: str
) -> AttachmentResult:
    """Attach the original file to the agent's draft; the same name and checksum is reused.

    A new attachment is read back from Odoo: its ``checksum`` must be the SHA-1 of the original
    file and it must hang on this draft, otherwise ``VerificationFailed`` (the original file is
    the evidence, so what Odoo keeps must be byte for byte what was received).
    """
    if not gw.is_agent_draft(bill_id):
        raise ForbiddenOperation(
            f"lampiran hanya pada draft tagihan agent (#{bill_id})",
            model="ir.attachment",
            method="create",
        )
    checksum = hashlib.sha1(content).hexdigest()  # noqa: S324 - Odoo's own checksum, not security
    rows = gw.search_read(
        "ir.attachment",
        [["res_model", "=", "account.move"], ["res_id", "=", bill_id], ["name", "=", name]],
        ["id", "checksum"],
    )
    same = [r for r in rows if r.get("checksum") == checksum]
    if same:
        return AttachmentResult(attachment_id=same[0]["id"], created=False, checksum=checksum)
    attachment_id = gw.create_attachment(bill_id, name, content, mimetype)
    stored = gw.read("ir.attachment", [attachment_id], ["id", "checksum", "res_model", "res_id"])
    if not stored:
        raise VerificationFailed(
            f"lampiran #{attachment_id} tidak terbaca ulang", model="ir.attachment", method="read"
        )
    row = stored[0]
    if (row.get("checksum"), row.get("res_model"), row.get("res_id")) != (
        checksum,
        "account.move",
        bill_id,
    ):
        raise VerificationFailed(
            f"lampiran #{attachment_id} tersimpan berbeda dari berkas asli "
            f"(checksum {row.get('checksum')}, seharusnya {checksum}; "
            f"induk {row.get('res_model')}#{row.get('res_id')})",
            model="ir.attachment",
            method="read",
        )
    return AttachmentResult(attachment_id=attachment_id, created=True, checksum=checksum)


class NoteResult(DomainModel):
    message_id: int
    created: bool
    marker: str


_KEY = re.compile(r"^[0-9a-f]{64}$")


def note_marker(idempotency_key: str) -> str:
    if not _KEY.match(idempotency_key):
        raise ValueError("kunci idempoten harus SHA-256 heksadesimal")
    return f"[faktur-beres {idempotency_key}]"


def post_note(gw: AgentOdooGateway, bill_id: int, text: str, idempotency_key: str) -> NoteResult:
    """An internal note (``mail.mt_note``) on the agent's draft, once per idempotency key.

    The key is written into the note as a marker; a note that already carries it is reused.
    """
    marker = note_marker(idempotency_key)
    if not gw.is_agent_draft(bill_id):
        raise ForbiddenOperation(
            f"catatan hanya pada draft tagihan agent (#{bill_id})",
            model="account.move",
            method="message_post",
        )
    rows = gw.search_read(
        "mail.message",
        [["model", "=", "account.move"], ["res_id", "=", bill_id], ["body", "ilike", marker]],
        ["id"],
        limit=1,
    )
    if rows:
        return NoteResult(message_id=rows[0]["id"], created=False, marker=marker)
    message_id = gw.post_note(bill_id, f"{text}\n\n{marker}")
    return NoteResult(message_id=message_id, created=True, marker=marker)


#: Field names that would hold an incoming tax invoice number (NSFP) on a vendor bill.
NSFP_FIELD_CANDIDATES = ("l10n_id_nsfp", "l10n_id_tax_number", "l10n_id_efaktur_number")


def vendor_bill_nsfp_field(gw: AgentOdooGateway) -> str | None:
    """The ``account.move`` field for the NSFP of a vendor bill, if this Odoo has one.

    Odoo 19's ``l10n_id_efaktur_coretax`` fields are for customer invoices (``out_invoice``);
    without a field for vendor bills the NSFP goes into the chatter note (RENCANA-TEKNIS §4.4).
    """
    fields = gw.fields_get("account.move", ["type", "string"])
    for name in NSFP_FIELD_CANDIDATES:
        if name in fields and fields[name].get("type") == "char":
            return name
    return None


def bill_note_text(
    invoice_number: str, nsfp: str | None, tax_invoice_date: date | None, file_name: str | None
) -> str:
    """The note on a new draft: source, and the NSFP that has no field on a vendor bill."""
    parts = [f"Draft tagihan dibuat Faktur Beres dari faktur {invoice_number}"]
    if file_name:
        parts[0] += f" (berkas {file_name})"
    parts[0] += "."
    if nsfp:
        when = f", tanggal {tax_invoice_date.isoformat()}" if tax_invoice_date else ""
        parts.append(f"Faktur pajak: NSFP {nsfp}{when}.")
    parts.append("Agent tidak memposting tagihan; posting oleh staf AP.")
    return " ".join(parts)
