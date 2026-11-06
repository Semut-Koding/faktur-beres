"""Turn the catalogue specs into printed documents and expected results.

Rules (docs/tugas/M1.md, M1.2):

* A new invoice is dated the day after the last receipt of its PO, but not after 2026-11-06.
  Scenarios may prescribe a date (duplicates, the vendor outside Odoo).
* Due date = invoice date + the vendor's payment term; tax-invoice date = invoice date.
* A corrected invoice in a vendor reply keeps the number and date of the original. It keeps the
  original NSFP too, unless the original had none or an old-format one: then it gets a new one.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from faktur_beres.dataset import Dataset
from faktur_beres.dataset.money import dpp_nilai_lain
from faktur_beres.generator.catalog import SCENARIO_PO, SCENARIOS, Doc, Item, Scenario
from faktur_beres.generator.determinism import rng
from faktur_beres.generator.emails import email_for
from faktur_beres.generator.model import (
    DocLine,
    EmailMeta,
    InvoiceDoc,
    Party,
    PrintedBankAccount,
)
from faktur_beres.generator.numbering import (
    NumberRequest,
    assign_invoice_numbers,
    assign_nsfp,
    dataset_accounts,
    dataset_npwps,
    group_digits,
    history_seq,
    old_nsfp,
    substitute_account,
    substitute_npwp,
)
from faktur_beres.generator.products import PRINTED

LATEST_ISSUE = date(2026, 11, 6)
OUTSIDE_TERM_DAYS = 30  # the vendor outside Odoo has no term in the dataset
REPLY_KEY = "/balasan"


@dataclass(frozen=True)
class ExpectedBillLine:
    po: str
    product: str
    qty: int
    price: int

    @property
    def po_line(self) -> str:
        return f"{self.po}/{self.product}"


@dataclass(frozen=True)
class ExpectedBill:
    vendor: str
    ref: str
    total: int
    lines: tuple[ExpectedBillLine, ...]


@dataclass(frozen=True)
class BuiltScenario:
    scenario: Scenario
    doc: InvoiceDoc
    reply_doc: InvoiceDoc | None
    reply_text: str | None
    email: EmailMeta | None
    bill: ExpectedBill | None

    @property
    def final_doc(self) -> InvoiceDoc:
        return self.reply_doc or self.doc


class CatalogBuilder:
    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self._vendors = {v.key: v for v in ds.vendors} | {v.key: v for v in ds.external_vendors}

    # -- small lookups -----------------------------------------------------------------------

    def bank_name(self, key: str) -> str:
        return self.ds.bank(key).name

    def issue_date(self, s: Scenario) -> date:
        if s.doc.issued is not None:
            return s.doc.issued
        po = self.ds.po_by_number(s.po)
        if po is None or not po.receipts:
            raise ValueError(f"{s.id}: PO {s.po} tanpa penerimaan dan tanpa tanggal tetap")
        received = max(r.received_on for r in po.receipts)
        return min(received + timedelta(days=1), LATEST_ISSUE)

    def term_days(self, vendor_key: str) -> int:
        vendor = self._vendors[vendor_key]
        return getattr(vendor, "payment_term_days", OUTSIDE_TERM_DAYS)

    # -- parts of a document ------------------------------------------------------------------

    def seller(self, s: Scenario, doc: Doc, substitute_npwps: dict[str, str]) -> Party:
        v = self._vendors[s.vendor]
        npwp = {
            "dataset": v.npwp,
            "kosong": None,
            "pengganti": substitute_npwps.get(s.id),
        }[doc.seller_npwp]
        return Party(v.name, npwp, v.street, v.city, v.state, v.zip, v.billing_email)

    def buyer(self) -> Party:
        c = self.ds.company
        return Party(c.name, c.npwp, c.street, c.city, c.state, c.zip, c.email_ap)

    def bank(self, s: Scenario, doc: Doc, new_accounts: dict[str, str]) -> PrintedBankAccount:
        v = self._vendors[s.vendor]
        registered = v.bank_account
        holder = v.name
        other_bank = next(
            b.key for b in sorted(self.ds.banks, key=lambda b: b.key) if b.key != registered.bank
        )
        match doc.bank:
            case "terdaftar":
                return PrintedBankAccount(
                    self.bank_name(registered.bank), registered.number, holder
                )
            case "terdaftar_berpemisah":
                number = group_digits(registered.number, (3, 3, 4), "-")
                return PrintedBankAccount(self.bank_name(registered.bank), number, holder)
            case "lain_bank_sama":
                bank_key = registered.bank
            case "lain_bank_lain" | "baru_injeksi":
                bank_key = other_bank
            case "baru_kirana":
                bank_key = "bank_kirana"
            case "milik_v05":
                other = self.ds.vendor("v05").bank_account
                return PrintedBankAccount(self.bank_name(other.bank), other.number, holder)
            case _:
                raise ValueError(f"{s.id}: mode rekening tidak dikenal {doc.bank}")
        return PrintedBankAccount(self.bank_name(bank_key), new_accounts[s.id], holder)

    def line(self, s: Scenario, it: Item) -> DocLine:
        if it.product is None:
            if it.free is None or it.price is None:
                raise ValueError(f"{s.id}: baris bebas butuh teks dan harga")
            code, description, unit, ubl_unit = it.free
            price = it.price
        else:
            printed = PRINTED[it.product]
            code = self.ds.product(it.product).vendor_code
            description, unit, ubl_unit = printed.description, printed.unit, printed.ubl_unit
            price = it.price if it.price is not None else self.po_price(s, it.product)
        if it.suffix:
            description = f"{description} {it.suffix}"
        amount = it.amount if it.amount is not None else it.qty * price
        return DocLine(code, description, it.qty, unit, price, amount, ubl_unit)

    def po_price(self, s: Scenario, product: str) -> int:
        po = self.ds.po_by_number(s.po) if s.po else None
        if po is not None:
            for line in po.lines:
                if line.product == product:
                    return line.unit_price
        return self.ds.product(product).purchase_price

    # -- whole documents ----------------------------------------------------------------------

    def document(
        self,
        s: Scenario,
        doc: Doc,
        *,
        number: str,
        issued: date,
        nsfp: str | None,
        substitute_npwps: dict[str, str],
        new_accounts: dict[str, str],
    ) -> InvoiceDoc:
        lines = tuple(self.line(s, it) for it in doc.items)
        subtotal = sum(line.amount for line in lines)
        if doc.tax == "nilai_lain":
            dpp = dpp_nilai_lain(subtotal)
            ppn = dpp * 12 // 100
            if dpp * 12 % 100:
                raise ValueError(f"{s.id}: PPN tidak bulat")
        else:  # PPN 12% of the selling price: the wrong base on purpose
            dpp = subtotal
            ppn = subtotal * 12 // 100
        bank = self.bank(s, doc, new_accounts)
        notes = tuple(
            note.format(bank_baru=bank.bank, rekening_baru=bank.number) for note in doc.notes
        )
        po_ref = s.po if doc.po_printed == SCENARIO_PO else doc.po_printed
        return InvoiceDoc(
            number=number,
            issue_date=issued,
            due_date=issued + timedelta(days=self.term_days(s.vendor)),
            po_ref=po_ref,
            seller=self.seller(s, doc, substitute_npwps),
            buyer=self.buyer(),
            lines=lines,
            subtotal=subtotal,
            dpp=dpp,
            ppn=ppn,
            total=subtotal + ppn,
            nsfp=nsfp,
            tax_invoice_date=issued if nsfp else None,
            bank=bank,
            notes=notes,
            hidden_texts=doc.hidden,
            photo_note=doc.photo_note,
            dpp_basis=doc.tax,
        )

    def build(self) -> list[BuiltScenario]:
        ds = self.ds
        issued = {s.id: self.issue_date(s) for s in SCENARIOS}
        formats = {key: v.invoice_number_format for key, v in self._vendors.items()}
        numbers = assign_invoice_numbers(
            (
                NumberRequest(s.id, s.vendor, issued[s.id], s.doc.number, s.doc.number_in_sequence)
                for s in SCENARIOS
            ),
            formats,
            history_seq(ds),
        )

        by_date = sorted(SCENARIOS, key=lambda s: (issued[s.id], s.id))
        nsfp_keys = [s.id for s in by_date if s.doc.nsfp == "baru"]
        nsfp_keys += [
            s.id + REPLY_KEY
            for s in by_date
            if s.reply and s.reply.doc and s.doc.nsfp in ("kosong", "lama")
        ]
        serials = assign_nsfp(nsfp_keys)
        for s in SCENARIOS:
            if s.doc.nsfp == "lama":
                serials[s.id] = old_nsfp(rng(s.id, "nsfp-lama").randint(10**6, 10**8 - 1))

        taken_npwp, substitute_npwps = dataset_npwps(ds), {}
        taken_accounts, new_accounts = dataset_accounts(ds), {}
        for s in SCENARIOS:
            if s.doc.seller_npwp == "pengganti":
                substitute_npwps[s.id] = substitute_npwp(s.id, taken_npwp)
                taken_npwp.add(substitute_npwps[s.id])
            if s.doc.bank in ("lain_bank_sama", "lain_bank_lain", "baru_kirana", "baru_injeksi"):
                new_accounts[s.id] = substitute_account(s.id, taken_accounts)
                taken_accounts.add(new_accounts[s.id])

        built = []
        for s in SCENARIOS:
            common = {
                "number": numbers[s.id],
                "issued": issued[s.id],
                "substitute_npwps": substitute_npwps,
                "new_accounts": new_accounts,
            }
            doc = self.document(s, s.doc, nsfp=serials.get(s.id), **common)
            reply_doc, reply_text = None, None
            if s.reply is not None:
                reply_text = s.reply.text.format(nomor=numbers[s.id])
                if s.reply.doc is not None:
                    reply_nsfp = serials.get(s.id + REPLY_KEY, doc.nsfp)
                    reply_doc = self.document(s, s.reply.doc, nsfp=reply_nsfp, **common)
            bill = self.expected_bill(s, reply_doc or doc)
            built.append(BuiltScenario(s, doc, reply_doc, reply_text, email_for(s, doc, ds), bill))
        return built

    def expected_bill(self, s: Scenario, final: InvoiceDoc) -> ExpectedBill | None:
        """The draft bill expected at the end: the final document's PO lines (BR-18)."""
        if s.final_status != "draft_created":
            return None
        spec = s.reply.doc if s.reply and s.reply.doc else s.doc
        lines = tuple(
            ExpectedBillLine(s.po, it.product, line.qty, line.unit_price)
            for it, line in zip(spec.items, final.lines, strict=True)
            if it.product is not None
        )
        return ExpectedBill(s.vendor, final.number, final.total, lines)


def build_catalog(ds: Dataset) -> list[BuiltScenario]:
    return CatalogBuilder(ds).build()
