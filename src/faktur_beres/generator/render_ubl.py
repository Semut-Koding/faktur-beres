"""Render an :class:`InvoiceDoc` as a UBL 2.1 Invoice and read it back (SPEC §15.6).

The mapping is the contract for the UBL parser of M3. Note the deliberate deviation from rule
BR-S-08 of EN 16931: ``cac:TaxSubtotal/cbc:TaxableAmount`` carries the *DPP nilai lain*
(11/12 of the selling price) with ``cbc:Percent`` 12, not the sum of the line amounts.
"""

from datetime import date
from typing import Any

from lxml import etree

from faktur_beres.generator.model import InvoiceDoc, Party, PrintedParties

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#extended#urn:fakturberes:id-ppn:1.0"
INVOICE_TYPE_CODE = "380"
CURRENCY = "IDR"
TAX_INVOICE_DESCRIPTION = "Faktur Pajak"
PAYMENT_MEANS_CODE = "30"  # credit transfer
VAT_RATE = "12"
#: Both parties carry their e-mail in cac:Contact.
PRINTED_PARTIES = PrintedParties(seller_email=True, buyer_email=True)


def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


class _Builder:
    def __init__(self) -> None:
        self.root = etree.Element(_q("inv", "Invoice"), nsmap={None: NS["inv"], **{
            k: v for k, v in NS.items() if k != "inv"}})  # fmt: skip

    def add(self, parent, prefix: str, name: str, text: str | None = None, **attrs):
        element = etree.SubElement(parent, _q(prefix, name), **attrs)
        if text is not None:
            element.text = text
        return element

    def amount(self, parent, name: str, value: int):
        return self.add(parent, "cbc", name, f"{value}.00", currencyID=CURRENCY)

    def tax_category(self, parent, name: str) -> None:
        category = self.add(parent, "cac", name)
        self.add(category, "cbc", "ID", "S")
        self.add(category, "cbc", "Percent", VAT_RATE)
        scheme = self.add(category, "cac", "TaxScheme")
        self.add(scheme, "cbc", "ID", "VAT")

    def party(self, parent, role: str, party: Party) -> None:
        wrapper = self.add(parent, "cac", role)
        node = self.add(wrapper, "cac", "Party")
        address = self.add(node, "cac", "PostalAddress")
        self.add(address, "cbc", "StreetName", party.street)
        self.add(address, "cbc", "CityName", party.city)
        self.add(address, "cbc", "PostalZone", party.zip)
        self.add(address, "cbc", "CountrySubentity", party.state)
        country = self.add(address, "cac", "Country")
        self.add(country, "cbc", "IdentificationCode", "ID")
        if party.npwp:
            tax = self.add(node, "cac", "PartyTaxScheme")
            self.add(tax, "cbc", "CompanyID", party.npwp)
            scheme = self.add(tax, "cac", "TaxScheme")
            self.add(scheme, "cbc", "ID", "VAT")
        legal = self.add(node, "cac", "PartyLegalEntity")
        self.add(legal, "cbc", "RegistrationName", party.name)
        if party.email:
            contact = self.add(node, "cac", "Contact")
            self.add(contact, "cbc", "ElectronicMail", party.email)


def render_ubl(doc: InvoiceDoc) -> bytes:
    """UBL 2.1 Invoice XML (UTF-8, pretty printed, deterministic)."""
    b = _Builder()
    root = b.root
    b.add(root, "cbc", "CustomizationID", CUSTOMIZATION_ID)
    b.add(root, "cbc", "ID", doc.number)
    b.add(root, "cbc", "IssueDate", doc.issue_date.isoformat())
    b.add(root, "cbc", "DueDate", doc.due_date.isoformat())
    b.add(root, "cbc", "InvoiceTypeCode", INVOICE_TYPE_CODE)
    for note in doc.notes:
        b.add(root, "cbc", "Note", note)
    b.add(root, "cbc", "DocumentCurrencyCode", CURRENCY)
    if doc.po_ref:
        order = b.add(root, "cac", "OrderReference")
        b.add(order, "cbc", "ID", doc.po_ref)
    if doc.nsfp:
        reference = b.add(root, "cac", "AdditionalDocumentReference")
        b.add(reference, "cbc", "ID", doc.nsfp)
        if doc.tax_invoice_date:
            b.add(reference, "cbc", "IssueDate", doc.tax_invoice_date.isoformat())
        b.add(reference, "cbc", "DocumentDescription", TAX_INVOICE_DESCRIPTION)
    b.party(root, "AccountingSupplierParty", doc.seller)
    b.party(root, "AccountingCustomerParty", doc.buyer)
    if doc.bank:
        means = b.add(root, "cac", "PaymentMeans")
        b.add(means, "cbc", "PaymentMeansCode", PAYMENT_MEANS_CODE)
        account = b.add(means, "cac", "PayeeFinancialAccount")
        b.add(account, "cbc", "ID", doc.bank.number)
        b.add(account, "cbc", "Name", doc.bank.holder)
        branch = b.add(account, "cac", "FinancialInstitutionBranch")
        b.add(branch, "cbc", "Name", doc.bank.bank)
    tax_total = b.add(root, "cac", "TaxTotal")
    b.amount(tax_total, "TaxAmount", doc.ppn)
    subtotal = b.add(tax_total, "cac", "TaxSubtotal")
    b.amount(subtotal, "TaxableAmount", doc.dpp if doc.dpp is not None else doc.subtotal)
    b.amount(subtotal, "TaxAmount", doc.ppn)
    b.tax_category(subtotal, "TaxCategory")
    totals = b.add(root, "cac", "LegalMonetaryTotal")
    b.amount(totals, "LineExtensionAmount", doc.subtotal)
    b.amount(totals, "TaxExclusiveAmount", doc.subtotal)
    b.amount(totals, "TaxInclusiveAmount", doc.total)
    b.amount(totals, "PayableAmount", doc.total)
    for index, line in enumerate(doc.lines, start=1):
        node = b.add(root, "cac", "InvoiceLine")
        b.add(node, "cbc", "ID", str(index))
        b.add(node, "cbc", "InvoicedQuantity", str(line.qty), unitCode=line.ubl_unit)
        b.amount(node, "LineExtensionAmount", line.amount)
        item = b.add(node, "cac", "Item")
        b.add(item, "cbc", "Name", line.description)
        if line.code:
            sellers = b.add(item, "cac", "SellersItemIdentification")
            b.add(sellers, "cbc", "ID", line.code)
        b.tax_category(item, "ClassifiedTaxCategory")
        price = b.add(node, "cac", "Price")
        b.amount(price, "PriceAmount", line.unit_price)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# -- reading back -------------------------------------------------------------------------------


def _text(node, path: str) -> str | None:
    found = node.xpath(path, namespaces=NS)
    return found[0].text if found else None


def _money(node, path: str) -> int | None:
    value = _text(node, path)
    if value is None:
        return None
    whole, _, cents = value.partition(".")
    if cents.strip("0"):
        raise ValueError(f"nilai {value} bukan rupiah bulat")
    return int(whole)


def _date(node, path: str) -> date | None:
    value = _text(node, path)
    return date.fromisoformat(value) if value else None


def _party(node, role: str) -> dict[str, Any]:
    base = f"cac:{role}/cac:Party"
    street = _text(node, f"{base}/cac:PostalAddress/cbc:StreetName")
    city = _text(node, f"{base}/cac:PostalAddress/cbc:CityName")
    state = _text(node, f"{base}/cac:PostalAddress/cbc:CountrySubentity")
    postal = _text(node, f"{base}/cac:PostalAddress/cbc:PostalZone")
    return {
        "nama": _text(node, f"{base}/cac:PartyLegalEntity/cbc:RegistrationName"),
        "npwp": _text(node, f"{base}/cac:PartyTaxScheme/cbc:CompanyID"),
        "alamat": f"{street}, {city}, {state} {postal}",
        "email": _text(node, f"{base}/cac:Contact/cbc:ElectronicMail"),
    }


def read_ubl(data: bytes) -> dict[str, Any]:
    """Read the fields of SPEC §15.6 back with XPath."""
    root = etree.fromstring(data)
    lines = []
    for line in root.xpath("cac:InvoiceLine", namespaces=NS):
        quantity = line.xpath("cbc:InvoicedQuantity", namespaces=NS)[0]
        lines.append(
            {
                "kode": _text(line, "cac:Item/cac:SellersItemIdentification/cbc:ID"),
                "deskripsi": _text(line, "cac:Item/cbc:Name"),
                "qty": int(quantity.text),
                "satuan": quantity.get("unitCode"),
                "harga_satuan": _money(line, "cac:Price/cbc:PriceAmount"),
                "jumlah": _money(line, "cbc:LineExtensionAmount"),
            }
        )
    bank = None
    if root.xpath("cac:PaymentMeans", namespaces=NS):
        account = "cac:PaymentMeans/cac:PayeeFinancialAccount"
        bank = {
            "bank": _text(root, f"{account}/cac:FinancialInstitutionBranch/cbc:Name"),
            "nomor": _text(root, f"{account}/cbc:ID"),
            "atas_nama": _text(root, f"{account}/cbc:Name"),
        }
    return {
        "nomor_faktur": _text(root, "cbc:ID"),
        "tanggal_faktur": _date(root, "cbc:IssueDate"),
        "jatuh_tempo": _date(root, "cbc:DueDate"),
        "nomor_po": _text(root, "cac:OrderReference/cbc:ID"),
        "penjual": _party(root, "AccountingSupplierParty"),
        "pembeli": _party(root, "AccountingCustomerParty"),
        "nsfp": _text(
            root,
            "cac:AdditionalDocumentReference[cbc:DocumentDescription="
            f"'{TAX_INVOICE_DESCRIPTION}']/cbc:ID",
        ),
        "tanggal_faktur_pajak": _date(root, "cac:AdditionalDocumentReference/cbc:IssueDate"),
        "rekening": bank,
        "baris": lines,
        "subtotal": _money(root, "cac:LegalMonetaryTotal/cbc:LineExtensionAmount"),
        "dpp_nilai_lain": _money(root, "cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount"),
        "ppn": _money(root, "cac:TaxTotal/cbc:TaxAmount"),
        "total": _money(root, "cac:LegalMonetaryTotal/cbc:PayableAmount"),
        "catatan": [n.text for n in root.xpath("cbc:Note", namespaces=NS)],
    }
