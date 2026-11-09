"""UBL 2.1 e-invoice parser (SPEC §15.6): ``InvoiceData``, free-text fragments, and remarks.

Agent code; it does not import the generator that writes these files.

Safe parsing. Only UTF-8 is read. Any ``<!`` declaration other than a comment or a CDATA section
(so every ``<!DOCTYPE`` and ``<!ENTITY``) is refused before parsing, and the parser target refuses
a doctype as well: no DTD, no entity expansion, no network access.

Values. Money and quantities come straight from the UBL decimal text as ``Decimal``; the unit is
the ``unitCode`` as written; the total is ``TaxInclusiveAmount``, and a different
``PayableAmount`` is a remark. The selling price is ``LegalMonetaryTotal/LineExtensionAmount`` and
the DPP nilai lain is ``TaxSubtotal/TaxableAmount`` (the deliberate BR-S-08 deviation of §15.6).

Validation errors (``UblInvalid`` → ``validation_failed``): not a UBL Invoice, a currency other than
IDR, a BR-02 field that is missing, a number or a date that cannot be read. Remarks, not errors: an
unknown ``CustomizationID`` or ``InvoiceTypeCode``, an embedded attachment (not processed).

Fragments (BR-15): ``cbc:Note`` → ``ubl_note``; item name and description → ``line_description``;
every other element text with a letter → ``document_field`` (``where`` is the element path); XML
comments and processing instructions → ``hidden_text``.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from faktur_beres.domain.invoice import (
    BankAccount,
    InvoiceData,
    InvoiceLine,
    Party,
    Seller,
    TextFragment,
    TextSource,
)

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
INVOICE_TAG = f"{{{NS['inv']}}}Invoice"
KNOWN_CUSTOMIZATION = "urn:cen.eu:en16931:2017#extended#urn:fakturberes:id-ppn:1.0"
KNOWN_TYPE_CODES = frozenset({"380"})
TAX_INVOICE_DESCRIPTION = "Faktur Pajak"
CURRENCY = "IDR"
VAT_SCHEME = "VAT"

_DECLARATION = re.compile(rb"<!(?!--|\[CDATA\[)")
_ENCODING = re.compile(rb"^(?:\xef\xbb\xbf)?\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)[\"']")
_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_INSTRUCTION = re.compile(r"<\?(?!xml[\s?])(.*?)\?>", re.DOTALL)
_DECIMAL = re.compile(r"[+-]?\d+(?:\.\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LETTER = re.compile(r"[^\W\d_]")
_SHOWN = 40  # characters of an unreadable value quoted in a problem


class UblError(Exception):
    """The UBL file cannot be used; it becomes ``validation_failed`` (BR-04: no re-read)."""

    kind = "validation_failed"

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


class UnsafeXml(UblError):
    """A declaration, another encoding, or XML that does not parse."""


class UblInvalid(UblError):
    """A UBL invoice whose content fails validation."""


@dataclass(frozen=True)
class UblReading:
    invoice: InvoiceData
    fragments: tuple[TextFragment, ...]
    remarks: tuple[str, ...]


class _RefuseDoctype(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise UnsafeXml(["DOCTYPE tidak diizinkan"])


def parse_xml(data: bytes) -> ET.Element:
    """Parse UTF-8 XML without any DTD; raise ``UnsafeXml``."""
    if _DECLARATION.search(data):
        raise UnsafeXml(["deklarasi <!DOCTYPE atau <!ENTITY tidak diizinkan"])
    declared = _ENCODING.match(data)
    if declared and declared.group(1).decode("ascii", "replace").lower() not in {"utf-8", "utf8"}:
        raise UnsafeXml(["XML harus UTF-8"])
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeXml(["XML harus UTF-8"]) from exc
    parser = ET.XMLParser(target=_RefuseDoctype())
    try:
        parser.feed(data)
        root: ET.Element = parser.close()
    except ET.ParseError as exc:
        raise UnsafeXml([f"XML tidak terbaca: {exc}"]) from exc
    return root


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _shown(value: str) -> str:
    return value if len(value) <= _SHOWN else value[:_SHOWN] + "…"


class _Reader:
    """Reads values and keeps the problems (errors) and remarks it meets."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.remarks: list[str] = []

    def text(self, node: ET.Element, path: str) -> str | None:
        found = node.find(path, NS)
        value = (found.text or "").strip() if found is not None else ""
        return value or None

    def required(self, node: ET.Element, path: str, label: str) -> str | None:
        value = self.text(node, path)
        if value is None:
            self.problems.append(f"{label} tidak ada")
        return value

    def decimal(self, raw: str, label: str) -> Decimal | None:
        value = raw.strip()
        if not _DECIMAL.fullmatch(value):
            self.problems.append(f"{label}: angka tidak terbaca ({_shown(value)!r})")
            return None
        return Decimal(value)

    def amount(self, node: ET.Element, path: str, label: str, *, required: bool) -> Decimal | None:
        found = node.find(path, NS)
        if found is None or not (found.text or "").strip():
            if required:
                self.problems.append(f"{label} tidak ada")
            return None
        currency = found.get("currencyID")
        if currency is not None and currency != CURRENCY:
            self.problems.append(f"{label}: mata uang {_shown(currency)}, bukan {CURRENCY}")
        return self.decimal(found.text or "", label)

    def day(self, node: ET.Element, path: str, label: str, *, required: bool) -> date | None:
        value = self.required(node, path, label) if required else self.text(node, path)
        if value is None:
            return None
        if _DATE.fullmatch(value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        self.problems.append(f"{label}: tanggal tidak terbaca ({_shown(value)!r})")
        return None


def _address(reader: _Reader, party: ET.Element) -> str | None:
    base = "cac:PostalAddress/cbc:"
    street, city, state, postal = (
        reader.text(party, base + name)
        for name in ("StreetName", "CityName", "CountrySubentity", "PostalZone")
    )
    tail = " ".join(part for part in (state, postal) if part)
    return ", ".join(part for part in (street, city, tail) if part) or None


def _vat_number(reader: _Reader, party: ET.Element, label: str) -> str | None:
    schemes = party.findall("cac:PartyTaxScheme", NS)
    for scheme in schemes:
        if reader.text(scheme, "cac:TaxScheme/cbc:ID") == VAT_SCHEME:
            return reader.text(scheme, "cbc:CompanyID")
    if schemes:
        reader.remarks.append(f"{label}: PartyTaxScheme tanpa skema {VAT_SCHEME} tidak dibaca")
    return None


def _party(reader: _Reader, root: ET.Element, role: str, label: str) -> Party:
    party = root.find(f"cac:{role}/cac:Party", NS)
    if party is None:
        return Party()
    return Party(
        name=reader.text(party, "cac:PartyLegalEntity/cbc:RegistrationName"),
        npwp=_vat_number(reader, party, label),
        address=_address(reader, party),
        email=reader.text(party, "cac:Contact/cbc:ElectronicMail"),
    )


def _tax_invoice(reader: _Reader, root: ET.Element) -> tuple[str | None, date | None]:
    references = [
        ref
        for ref in root.findall("cac:AdditionalDocumentReference", NS)
        if reader.text(ref, "cbc:DocumentDescription") == TAX_INVOICE_DESCRIPTION
    ]
    if not references:
        return None, None
    if len(references) > 1:
        reader.remarks.append("lebih dari satu referensi Faktur Pajak; yang pertama dibaca")
    first = references[0]
    return (
        reader.text(first, "cbc:ID"),
        reader.day(first, "cbc:IssueDate", "tanggal faktur pajak", required=False),
    )


def _bank_account(reader: _Reader, root: ET.Element) -> BankAccount | None:
    accounts = root.findall("cac:PaymentMeans/cac:PayeeFinancialAccount", NS)
    if not accounts:
        return None
    if len(accounts) > 1:
        reader.remarks.append("lebih dari satu rekening pembayaran; yang pertama dibaca")
    account = accounts[0]
    number = reader.text(account, "cbc:ID")
    if number is None:
        reader.remarks.append("rekening pembayaran tanpa nomor tidak dibaca")
        return None
    return BankAccount(
        bank=reader.text(account, "cac:FinancialInstitutionBranch/cbc:Name"),
        number=number,
        holder=reader.text(account, "cbc:Name"),
    )


def _line(reader: _Reader, node: ET.Element, number: int) -> InvoiceLine | None:
    label = f"baris {number}"
    problems_before = len(reader.problems)
    description = reader.text(node, "cac:Item/cbc:Name") or reader.text(
        node, "cac:Item/cbc:Description"
    )
    if description is None:
        reader.problems.append(f"{label}: deskripsi tidak ada")
    quantity_node = node.find("cbc:InvoicedQuantity", NS)
    quantity = unit = None
    if quantity_node is None or not (quantity_node.text or "").strip():
        reader.problems.append(f"{label}: kuantitas tidak ada")
    else:
        quantity = reader.decimal(quantity_node.text or "", f"{label}: kuantitas")
        unit = (quantity_node.get("unitCode") or "").strip() or None
        if unit is None:
            reader.problems.append(f"{label}: satuan (unitCode) tidak ada")
    unit_price = reader.amount(
        node, "cac:Price/cbc:PriceAmount", f"{label}: harga satuan", required=True
    )
    amount = reader.amount(
        node, "cbc:LineExtensionAmount", f"{label}: subtotal baris", required=True
    )
    base_quantity = reader.text(node, "cac:Price/cbc:BaseQuantity")
    if base_quantity is not None and base_quantity not in {"1", "1.0", "1.00"}:
        reader.remarks.append(f"{label}: BaseQuantity {_shown(base_quantity)} tidak dipakai")
    if len(reader.problems) > problems_before:
        return None
    assert description and quantity is not None and unit and unit_price is not None
    assert amount is not None
    return InvoiceLine(
        vendor_code=reader.text(node, "cac:Item/cac:SellersItemIdentification/cbc:ID"),
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        amount=amount,
    )


def _check_header(reader: _Reader, root: ET.Element) -> None:
    customization = reader.text(root, "cbc:CustomizationID")
    if customization != KNOWN_CUSTOMIZATION:
        reader.remarks.append(f"CustomizationID tidak dikenal: {_shown(customization or '-')}")
    type_code = reader.text(root, "cbc:InvoiceTypeCode")
    if type_code not in KNOWN_TYPE_CODES:
        reader.remarks.append(f"InvoiceTypeCode tidak dikenal: {_shown(type_code or '-')}")
    currency = reader.text(root, "cbc:DocumentCurrencyCode")
    if currency != CURRENCY:
        shown = _shown(currency) if currency else "tidak ada"
        reader.problems.append(f"mata uang dokumen {shown}, bukan {CURRENCY}")
    embedded = root.findall(".//cbc:EmbeddedDocumentBinaryObject", NS)
    if embedded:
        reader.remarks.append(f"{len(embedded)} lampiran tertanam tidak diproses")
    if len(root.findall("cac:TaxTotal/cac:TaxSubtotal", NS)) > 1:
        reader.remarks.append("lebih dari satu TaxSubtotal; yang pertama dibaca")


def _invoice(reader: _Reader, root: ET.Element) -> InvoiceData | None:
    _check_header(reader, root)
    number = reader.required(root, "cbc:ID", "nomor faktur")
    issued = reader.day(root, "cbc:IssueDate", "tanggal faktur", required=True)
    seller = _party(reader, root, "AccountingSupplierParty", "penjual")
    if not seller.name:
        reader.problems.append("nama penjual tidak ada")
    buyer = _party(reader, root, "AccountingCustomerParty", "pembeli")
    nodes = root.findall("cac:InvoiceLine", NS)
    if not nodes:
        reader.problems.append("tidak ada baris faktur")
    lines = [_line(reader, node, index) for index, node in enumerate(nodes, start=1)]
    totals = "cac:LegalMonetaryTotal/cbc:"
    subtotal = reader.amount(root, totals + "LineExtensionAmount", "subtotal", required=True)
    vat = reader.amount(root, "cac:TaxTotal/cbc:TaxAmount", "PPN", required=True)
    total = reader.amount(root, totals + "TaxInclusiveAmount", "total", required=True)
    payable = reader.amount(root, totals + "PayableAmount", "PayableAmount", required=False)
    if payable is not None and total is not None and payable != total:
        reader.remarks.append(f"PayableAmount {payable} berbeda dari TaxInclusiveAmount {total}")
    dpp = reader.amount(
        root, "cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount", "DPP nilai lain", required=False
    )
    nsfp, tax_invoice_date = _tax_invoice(reader, root)
    bank_account = _bank_account(reader, root)
    due = reader.day(root, "cbc:DueDate", "jatuh tempo", required=False)
    if reader.problems:
        return None
    assert number and issued and subtotal is not None and vat is not None and total is not None
    try:
        return InvoiceData(
            invoice_number=number,
            invoice_date=issued,
            due_date=due,
            po_number=reader.text(root, "cac:OrderReference/cbc:ID"),
            seller=Seller(**seller.model_dump()),
            buyer=buyer if buyer != Party() else None,
            nsfp=nsfp,
            tax_invoice_date=tax_invoice_date,
            bank_account=bank_account,
            lines=tuple(line for line in lines if line is not None),
            subtotal=subtotal,
            dpp_nilai_lain=dpp,
            vat=vat,
            total=total,
            notes=tuple(
                text for note in root.findall("cbc:Note", NS) if (text := (note.text or "").strip())
            ),
        )
    except ValidationError as exc:
        reader.problems += [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return None


def _fragments(root: ET.Element, source_text: str) -> list[TextFragment]:
    fragments: list[TextFragment] = []
    handled: set[int] = set()
    for number, note in enumerate(root.findall("cbc:Note", NS), start=1):
        handled.add(id(note))
        if text := (note.text or "").strip():
            fragments.append(
                TextFragment(source=TextSource.UBL_NOTE, text=text, where=f"catatan {number}")
            )
    for number, line in enumerate(root.findall("cac:InvoiceLine", NS), start=1):
        for path in ("cac:Item/cbc:Name", "cac:Item/cbc:Description"):
            for node in line.findall(path, NS):
                handled.add(id(node))
                if text := (node.text or "").strip():
                    fragments.append(
                        TextFragment(
                            source=TextSource.LINE_DESCRIPTION, text=text, where=f"baris {number}"
                        )
                    )
    handled.update(id(node) for node in root.findall(".//cbc:EmbeddedDocumentBinaryObject", NS))

    def walk(node: ET.Element, path: str) -> None:
        for child in node:
            child_path = f"{path}/{_local(child.tag)}" if path else _local(child.tag)
            if id(child) not in handled:
                text = (child.text or "").strip()
                if _LETTER.search(text):
                    fragments.append(
                        TextFragment(source=TextSource.DOCUMENT_FIELD, text=text, where=child_path)
                    )
                walk(child, child_path)
            tail = (child.tail or "").strip()
            if _LETTER.search(tail):
                fragments.append(
                    TextFragment(
                        source=TextSource.DOCUMENT_FIELD, text=tail, where=path or "Invoice"
                    )
                )

    walk(root, "")
    for number, match in enumerate(_COMMENT.finditer(source_text), start=1):
        if text := match.group(1).strip():
            fragments.append(
                TextFragment(
                    source=TextSource.HIDDEN_TEXT, text=text, where=f"komentar XML {number}"
                )
            )
    for number, match in enumerate(_INSTRUCTION.finditer(source_text), start=1):
        if text := match.group(1).strip():
            fragments.append(
                TextFragment(
                    source=TextSource.HIDDEN_TEXT,
                    text=text,
                    where=f"instruksi pemrosesan XML {number}",
                )
            )
    return fragments


def parse_ubl(data: bytes) -> UblReading:
    """Read a UBL 2.1 Invoice; raise ``UnsafeXml`` or ``UblInvalid`` (``validation_failed``)."""
    root = parse_xml(data)
    if root.tag != INVOICE_TAG:
        raise UblInvalid([f"bukan UBL 2.1 Invoice (elemen akar {_shown(_local(root.tag))})"])
    reader = _Reader()
    invoice = _invoice(reader, root)
    if invoice is None:
        raise UblInvalid(reader.problems)
    fragments = _fragments(root, data.decode("utf-8-sig"))
    return UblReading(invoice=invoice, fragments=tuple(fragments), remarks=tuple(reader.remarks))
