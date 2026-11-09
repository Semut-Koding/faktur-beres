"""From the printed text Gemini read (``ExtractedInvoice``) to ``InvoiceData``, by code (M3.4).

The number style is decided once per document (``document_number_style`` over every numeric text),
then ``parse_number`` and ``parse_date`` read the values and the BR-02 required fields are checked.
NPWP and NSFP stay as printed; ``tax_ids`` checks their format. The result is the ``InvoiceData``
(when it can be built) and the list of problems, each with the field path (``baris[2].jumlah``), the
kind, and the text as read:

* ``missing_required``: a required field is empty (BR-02);
* ``unreadable``: a number or a date cannot be read;
* ``ambiguous``: the number style is unclear (a lone ``4.850``, or two styles in one document);
* ``arithmetic``: from ``check_arithmetic`` (BR-03), on every field of the equation;
* ``npwp_format`` and ``nsfp_format``: from ``tax_ids``.

The first three block the ``InvoiceData``; the last three leave it as printed for the rules.
``values`` holds the normalised value of every field, which BR-04 compares between two readings.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.findings import DocumentSumEvidence, LineAmountEvidence
from faktur_beres.domain.invoice import BankAccount, InvoiceData, InvoiceLine, Party, Seller
from faktur_beres.domain.normalize import (
    AmbiguousNumberError,
    InvalidDateError,
    NormalizationError,
    NumberStyle,
    document_number_style,
    number_style,
    parse_date,
    parse_number,
    tax_id_digits,
)
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.rules.arithmetic import check_arithmetic
from faktur_beres.domain.tax_ids import npwp_problem, nsfp_problem
from faktur_beres.extraction.schema import (
    ExtractedBankAccount,
    ExtractedInvoice,
    ExtractedLine,
    ExtractedParty,
)


class ProblemKind(StrEnum):
    MISSING_REQUIRED = "missing_required"
    UNREADABLE = "unreadable"
    AMBIGUOUS = "ambiguous"
    ARITHMETIC = "arithmetic"
    NPWP_FORMAT = "npwp_format"
    NSFP_FORMAT = "nsfp_format"


#: Problems that leave a value usable as printed: the rules decide (B6, B3).
ACCEPTED_AS_PRINTED = frozenset(
    {ProblemKind.ARITHMETIC, ProblemKind.NPWP_FORMAT, ProblemKind.NSFP_FORMAT}
)


class FieldProblem(DomainModel):
    path: str  # e.g. "baris[2].jumlah", "penjual.npwp"
    kind: ProblemKind
    text: str | None  # as read; never a computed value


@dataclass(frozen=True)
class Conversion:
    invoice: InvoiceData | None
    problems: tuple[FieldProblem, ...]
    values: Mapping[str, str | None]  # normalised value of every field, by path
    texts: Mapping[str, str | None]  # the text as read, by path

    @property
    def blocking(self) -> tuple[FieldProblem, ...]:
        return tuple(p for p in self.problems if p.kind not in ACCEPTED_AS_PRINTED)


_JSON_FIELDS = {
    "nomor_faktur": "invoice_number",
    "tanggal_faktur": "invoice_date",
    "jatuh_tempo": "due_date",
    "nomor_po": "po_number",
    "penjual": "seller",
    "pembeli": "buyer",
    "nama": "name",
    "npwp": "npwp",
    "alamat": "address",
    "email": "email",
    "nsfp": "nsfp",
    "tanggal_faktur_pajak": "tax_invoice_date",
    "rekening": "bank_account",
    "bank": "bank",
    "nomor": "number",
    "atas_nama": "holder",
    "baris": "lines",
    "kode": "vendor_code",
    "deskripsi": "description",
    "qty": "quantity",
    "satuan": "unit",
    "harga_satuan": "unit_price",
    "jumlah": "amount",
    "subtotal": "subtotal",
    "dpp_nilai_lain": "dpp_nilai_lain",
    "ppn": "vat",
    "total": "total",
}
_LINE_INDEX = re.compile(r"\[(\d+)\]")


def json_path(path: str) -> str:
    """The path in the extraction JSON: ``baris[2].jumlah`` → ``lines[1].amount``."""
    parts = []
    for part in path.split("."):
        name, _, index = part.partition("[")
        json_name = _JSON_FIELDS.get(name, name)
        parts.append(f"{json_name}[{int(index.rstrip(']')) - 1}]" if index else json_name)
    return ".".join(parts)


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    value = " ".join(text.split())
    return value or None


def _compact(text: str) -> str:
    return "".join(text.split())


def _number_key(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def _style_of(texts: Iterable[str]) -> tuple[NumberStyle | None, bool]:
    """The document's number style and whether it mixes two styles."""
    readable = []
    for text in texts:
        try:
            number_style(text)
        except NormalizationError:
            continue
        readable.append(text)
    try:
        return document_number_style(readable), False
    except AmbiguousNumberError:
        return None, True


class _Converter:
    def __init__(self, extracted: ExtractedInvoice) -> None:
        self.x = extracted
        self.problems: list[FieldProblem] = []
        self.values: dict[str, str | None] = {}
        self.texts: dict[str, str | None] = {}
        numeric = [extracted.subtotal, extracted.dpp_nilai_lain, extracted.vat, extracted.total]
        for line in extracted.lines:
            numeric += [line.quantity, line.unit_price, line.amount]
        self.style, self.mixed = _style_of(t for t in numeric if t and t.strip())

    def problem(self, path: str, kind: ProblemKind, text: str | None) -> None:
        self.problems.append(FieldProblem(path=path, kind=kind, text=text))

    def text(self, path: str, raw: str | None, *, required: bool = False) -> str | None:
        value = _clean(raw)
        self.texts[path] = self.values[path] = value
        if value is None and required:
            self.problem(path, ProblemKind.MISSING_REQUIRED, None)
        return value

    def number(self, path: str, raw: str | None, *, required: bool) -> Decimal | None:
        value = _clean(raw)
        self.texts[path] = value
        if value is None:
            self.values[path] = None
            if required:
                self.problem(path, ProblemKind.MISSING_REQUIRED, None)
            return None
        self.values[path] = _compact(value)
        if self.mixed:
            self.problem(path, ProblemKind.AMBIGUOUS, value)
            return None
        try:
            number = parse_number(value, self.style)
        except AmbiguousNumberError:
            self.problem(path, ProblemKind.AMBIGUOUS, value)
            return None
        except NormalizationError:
            self.problem(path, ProblemKind.UNREADABLE, value)
            return None
        self.values[path] = _number_key(number)
        return number

    def day(self, path: str, raw: str | None, *, required: bool) -> date | None:
        value = _clean(raw)
        self.texts[path] = value
        if value is None:
            self.values[path] = None
            if required:
                self.problem(path, ProblemKind.MISSING_REQUIRED, None)
            return None
        try:
            parsed = parse_date(value)
        except InvalidDateError:
            self.values[path] = _compact(value)
            self.problem(path, ProblemKind.UNREADABLE, value)
            return None
        self.values[path] = parsed.isoformat()
        return parsed

    def tax_id(self, path: str, raw: str | None, kind: ProblemKind) -> str | None:
        value = _clean(raw)
        self.texts[path] = value
        self.values[path] = (tax_id_digits(value) or _compact(value)) if value else None
        if value is not None:
            check = npwp_problem if kind is ProblemKind.NPWP_FORMAT else nsfp_problem
            if check(value) is not None:
                self.problem(path, kind, value)
        return value

    def party(self, prefix: str, party: ExtractedParty | None, *, required: bool) -> Party:
        return Party(
            name=self.text(f"{prefix}.nama", party.name if party else None, required=required),
            npwp=self.tax_id(
                f"{prefix}.npwp", party.npwp if party else None, ProblemKind.NPWP_FORMAT
            ),
            address=self.text(f"{prefix}.alamat", party.address if party else None),
            email=self.text(f"{prefix}.email", party.email if party else None),
        )

    def bank_account(self, account: ExtractedBankAccount | None) -> BankAccount | None:
        bank = self.text("rekening.bank", account.bank if account else None)
        number = self.text("rekening.nomor", account.number if account else None)
        holder = self.text("rekening.atas_nama", account.holder if account else None)
        if number is not None:
            self.values["rekening.nomor"] = re.sub(r"\D", "", number) or _compact(number)
        return BankAccount(bank=bank, number=number, holder=holder) if number else None

    def line(self, number: int, line: ExtractedLine) -> InvoiceLine | None:
        at = f"baris[{number}]"
        code = self.text(f"{at}.kode", line.vendor_code)
        description = self.text(f"{at}.deskripsi", line.description, required=True)
        quantity = self.number(f"{at}.qty", line.quantity, required=True)
        unit = self.text(f"{at}.satuan", line.unit, required=True)
        unit_price = self.number(f"{at}.harga_satuan", line.unit_price, required=True)
        amount = self.number(f"{at}.jumlah", line.amount, required=True)
        if description is None or quantity is None or unit is None:
            return None
        if unit_price is None or amount is None:
            return None
        return InvoiceLine(
            vendor_code=code,
            description=description,
            quantity=quantity,
            unit=unit,
            unit_price=unit_price,
            amount=amount,
        )

    def invoice(self) -> InvoiceData | None:
        x = self.x
        number = self.text("nomor_faktur", x.invoice_number, required=True)
        issued = self.day("tanggal_faktur", x.invoice_date, required=True)
        due = self.day("jatuh_tempo", x.due_date, required=False)
        po_number = self.text("nomor_po", x.po_number)
        seller = self.party("penjual", x.seller, required=True)
        buyer = self.party("pembeli", x.buyer, required=False)
        nsfp = self.tax_id("nsfp", x.nsfp, ProblemKind.NSFP_FORMAT)
        tax_date = self.day("tanggal_faktur_pajak", x.tax_invoice_date, required=False)
        bank = self.bank_account(x.bank_account)
        self.values["baris"] = str(len(x.lines))  # the line count, compared like a field
        if not x.lines:
            self.problem("baris", ProblemKind.MISSING_REQUIRED, None)
        lines = [self.line(i, line) for i, line in enumerate(x.lines, start=1)]
        subtotal = self.number("subtotal", x.subtotal, required=True)
        dpp = self.number("dpp_nilai_lain", x.dpp_nilai_lain, required=False)
        vat = self.number("ppn", x.vat, required=True)
        total = self.number("total", x.total, required=True)
        notes = tuple(text for note in x.notes if (text := _clean(note)))
        if any(p.kind not in ACCEPTED_AS_PRINTED for p in self.problems):
            return None
        assert number and issued and seller.name and subtotal is not None
        assert vat is not None and total is not None
        try:
            return InvoiceData(
                invoice_number=number,
                invoice_date=issued,
                due_date=due,
                po_number=po_number,
                seller=Seller(**seller.model_dump()),
                buyer=buyer if buyer != Party() else None,
                nsfp=nsfp,
                tax_invoice_date=tax_date,
                bank_account=bank,
                lines=tuple(line for line in lines if line is not None),
                subtotal=subtotal,
                dpp_nilai_lain=dpp,
                vat=vat,
                total=total,
                notes=notes,
            )
        except ValidationError as exc:  # pragma: no cover - the checks above come first
            for error in exc.errors():
                self.problem(".".join(map(str, error["loc"])), ProblemKind.UNREADABLE, None)
            return None

    def arithmetic(self, invoice: InvoiceData, policy: PolicyConfig) -> None:
        seen: set[str] = set()
        line_count = len(invoice.lines)
        for finding in check_arithmetic(invoice, policy):
            evidence = finding.evidence
            if isinstance(evidence, LineAmountEvidence):
                at = f"baris[{evidence.line}]"
                paths = [f"{at}.qty", f"{at}.harga_satuan", f"{at}.jumlah"]
            elif isinstance(evidence, DocumentSumEvidence) and evidence.subject == "subtotal":
                paths = [f"baris[{i}].jumlah" for i in range(1, line_count + 1)] + ["subtotal"]
            else:
                paths = ["subtotal", "ppn", "total"]
            for path in paths:
                if path not in seen:
                    seen.add(path)
                    self.problem(path, ProblemKind.ARITHMETIC, self.texts.get(path))


def convert(extracted: ExtractedInvoice, policy: PolicyConfig) -> Conversion:
    """Read the numbers and dates by code and list every problem (BR-02, BR-03, BR-10 format)."""
    converter = _Converter(extracted)
    invoice = converter.invoice()
    if invoice is not None:
        converter.arithmetic(invoice, policy)
    return Conversion(
        invoice=invoice,
        problems=tuple(converter.problems),
        values=dict(converter.values),
        texts=dict(converter.texts),
    )
