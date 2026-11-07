"""Tests for the scenario catalogue: composition, SPEC §15.3 and §15.3.1, and document rules."""

import re
from collections import Counter
from datetime import timedelta

import pytest
from stdnum.id import npwp as stdnum_npwp

from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset import load_dataset
from faktur_beres.generator.catalog import SCENARIOS
from faktur_beres.generator.documents import LATEST_ISSUE, BuiltScenario, build_catalog
from faktur_beres.generator.numbering import (
    dataset_accounts,
    dataset_npwps,
    history_seq,
    normalize_number,
    parse_seq,
)

LABEL_PATTERN = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")
CATEGORY_KEYS = (
    "cocok",
    "selisih_harga",
    "kuantitas_kurang",
    "duplikat",
    "po_tidak_ditemukan",
    "pajak_tidak_lengkap",
    "rekening_berubah",
    "injeksi_prompt",
)
PREFIX_CATEGORY = dict(
    zip(("COC", "HRG", "QTY", "DUP", "NPO", "PJK", "REK", "INJ"), CATEGORY_KEYS, strict=True)
)


@pytest.fixture(scope="module")
def ds():
    return load_dataset()


@pytest.fixture(scope="module")
def built(ds) -> dict[str, BuiltScenario]:
    return {b.scenario.id: b for b in build_catalog(ds)}


def table_rows(path, start: str, stop: str) -> list[list[str]]:
    text = path.read_text(encoding="utf-8")
    section = text.split(start, 1)[1].split(stop, 1)[0]
    rows = []
    for line in section.splitlines():
        if re.match(r"^\| [A-Z]{3}-\d{2} \|", line):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


SPEC = REPO_ROOT / "docs" / "SPEC.md"


def spec_table() -> dict[str, list[str]]:
    """The catalogue table of SPEC §15.3 (ends before the data needs; §15.3.1 is separate)."""
    rows = table_rows(SPEC, "### 15.3 Katalog skenario uji", "**Kebutuhan")
    assert rows and len(rows[0]) == 6, "tabel katalog §15.3 berubah bentuk"
    return {row[0]: row for row in rows}


def detail_table() -> dict[str, list[str]]:
    """The per-scenario document details of SPEC §15.3.1 (copied there from the M1 task)."""
    rows = table_rows(SPEC, "#### 15.3.1 Rincian dokumen per skenario", "Ketentuan isi")
    assert rows and len(rows[0]) == 5, "tabel rincian §15.3.1 berubah bentuk"
    return {row[0]: row for row in rows}


def all_docs(built):
    for b in built.values():
        yield b.scenario.id, "asli", b.doc
        if b.reply_doc is not None:
            yield b.scenario.id, "balasan", b.reply_doc


# -- composition ----------------------------------------------------------------------------------


def test_forty_unique_ids_with_matching_category() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == 40 and len(set(ids)) == 40
    for s in SCENARIOS:
        assert re.fullmatch(r"[A-Z]{3}-0[1-5]", s.id), s.id
        assert s.category == PREFIX_CATEGORY[s.id[:3]], s.id
    assert Counter(s.category for s in SCENARIOS) == {key: 5 for key in CATEGORY_KEYS}


def test_composition() -> None:
    assert Counter(s.format for s in SCENARIOS) == {"pdf": 24, "gambar": 11, "ubl": 5}
    assert Counter(s.channel for s in SCENARIOS) == {"email": 20, "unggahan": 20}
    for key in CATEGORY_KEYS:
        channels = Counter(s.channel for s in SCENARIOS if s.category == key)
        assert channels["email"] >= 2 and channels["unggahan"] >= 2, key
    assert Counter(s.route for s in SCENARIOS) == {"A": 9, "B": 15, "C": 16}
    styles = Counter(s.image_style for s in SCENARIOS if s.format == "gambar")
    assert styles["pindaian"] >= 2 and sum(styles.values()) == 11
    assert all(s.image_style is None for s in SCENARIOS if s.format != "gambar")


def test_replies_and_decisions_follow_the_route() -> None:
    for s in SCENARIOS:
        if s.route == "B" and s.final_status == "draft_created":
            assert s.reply is not None, s.id
        else:
            assert s.reply is None, s.id
        assert (s.decision is not None) == (s.route == "C"), s.id
        if s.decision == "setujui":
            assert s.final_status == "draft_created" and s.idempotency, s.id
        if s.decision == "tolak":
            assert s.final_status == "rejected", s.id
    replies = Counter(s.reply.kind for s in SCENARIOS if s.reply)
    assert replies == {"lampiran": 11, "teks": 2}
    assert {s.id for s in SCENARIOS if s.idempotency} == {"COC-04", "COC-05"}


# -- SPEC §15.3 and §15.3.1 ---------------------------------------------------------------------


def test_catalogue_matches_spec_table(ds) -> None:
    spec = spec_table()
    assert set(spec) == {s.id for s in SCENARIOS}
    for s in SCENARIOS:
        _, _, fmt, po_cell, route_cell, reason_cell = spec[s.id]
        assert {"PDF": "pdf", "Gambar": "gambar", "UBL": "ubl"}[fmt] == s.format, s.id
        if po_cell == "-":
            assert s.po is None, s.id
        else:
            number, owner = re.fullmatch(r"(P\d{5}) \((v\d{2})\)", po_cell).groups()
            assert s.po == number, s.id
            assert ds.po_by_number(number).vendor == owner, s.id
        route, status = re.fullmatch(r"([ABC]) → `(\w+)`", route_cell).groups()
        assert (s.route, s.final_status) == (route, status), s.id
        extra_part = " ".join(re.findall(r"\(juga ([^)]*)\)", reason_cell))
        extra = tuple(re.findall(r"`([a-z_]+)`", extra_part))
        required = tuple(c for c in re.findall(r"`([a-z_]+)`", reason_cell) if c not in extra)
        assert s.reasons == required, s.id
        assert s.extra_reasons == extra, s.id


def test_catalogue_matches_detail_table(built) -> None:
    table = detail_table()
    assert set(table) == set(built)
    for sid, b in built.items():
        _, channel, _, _, bill_cell = table[sid]
        assert b.scenario.channel == channel, sid
        if bill_cell == "-":
            assert b.bill is None, sid
            continue
        po = re.match(r"(P\d{5})", bill_cell).group(1)
        total = int(re.findall(r"Rp([\d.]+)", bill_cell)[-1].replace(".", ""))
        assert b.bill is not None, sid
        assert {line.po for line in b.bill.lines} == {po}, sid
        assert b.bill.total == total, sid


# -- document rules -------------------------------------------------------------------------------


def test_amounts_are_whole_and_consistent(built) -> None:
    inconsistent, off_rule = set(), set()
    for sid, kind, doc in all_docs(built):
        assert doc.subtotal % 300 == 0, (sid, kind)
        if not doc.arithmetic_consistent():
            inconsistent.add((sid, kind))
        if not doc.tax_rule_consistent():
            off_rule.add((sid, kind))
    assert inconsistent == {("HRG-05", "asli")}
    assert off_rule == {("PJK-04", "asli")}


def test_expected_bills_fit_open_quantities(ds, built) -> None:
    for sid, b in built.items():
        if b.bill is None:
            continue
        assert b.bill.ref == b.doc.number, sid
        assert b.bill.vendor == b.scenario.vendor, sid
        assert b.bill.total == b.final_doc.total, sid
        for line in b.bill.lines:
            po = ds.po_by_number(line.po)
            assert po.vendor == b.scenario.vendor, sid
            open_qty = po.received_qty(line.product) - po.billed_qty(line.product)
            assert 0 < line.qty <= open_qty, sid


def test_dates(ds, built) -> None:
    for sid, b in built.items():
        s, doc = b.scenario, b.doc
        if s.doc.issued is None:
            received = max(r.received_on for r in ds.po_by_number(s.po).receipts)
            assert doc.issue_date == min(received + timedelta(days=1), LATEST_ISSUE), sid
        assert doc.issue_date <= LATEST_ISSUE, sid
        assert doc.due_date == doc.issue_date + timedelta(days=30), sid
        if doc.nsfp:
            assert doc.tax_invoice_date == doc.issue_date, sid
        if b.reply_doc is not None:
            assert (b.reply_doc.number, b.reply_doc.issue_date) == (doc.number, doc.issue_date)
    assert built["NPO-05"].doc.issue_date.isoformat() == "2026-11-04"


def test_invoice_numbers(ds, built) -> None:
    formats = {v.key: v.invoice_number_format for v in ds.vendors}
    formats |= {v.key: v.invoice_number_format for v in ds.external_vendors}
    history = history_seq(ds)
    fixed_out_of_sequence = {
        s.id for s in SCENARIOS if s.doc.number and not s.doc.number_in_sequence
    }
    per_vendor: dict[str, list] = {}
    for sid, b in built.items():
        if sid in fixed_out_of_sequence:
            continue
        seq = parse_seq(formats[b.scenario.vendor], b.doc.number)
        assert seq is not None and seq > history.get(b.scenario.vendor, 0), sid
        per_vendor.setdefault(b.scenario.vendor, []).append((b.doc.issue_date, sid, seq))
    for vendor, rows in per_vendor.items():
        seqs = [seq for _, _, seq in sorted(rows)]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), vendor

    owners: dict[str, set[str]] = {}
    for po, bill in ds.history_bills:
        owners.setdefault(normalize_number(bill.vendor_invoice_number), set()).add(
            f"riwayat:{po.vendor}"
        )
    for sid, b in built.items():
        owners.setdefault(normalize_number(b.doc.number), set()).add(f"{sid}:{b.scenario.vendor}")
    shared = {key: owner for key, owner in owners.items() if len(owner) > 1}
    assert shared == {
        "TWLINV26100153": {"riwayat:v02", "DUP-01:v02"},
        "LKP26100088": {"riwayat:v07", "DUP-02:v07"},
        "RPK1026031": {"riwayat:v05", "DUP-05:v05"},
        "INV20260412": {"riwayat:v06", "DUP-04:v08"},
    }
    assert built["DUP-03"].doc.number == "PAS-24519-R"


def test_nsfp(built) -> None:
    seen: dict[str, str] = {}
    for sid, kind, doc in all_docs(built):
        if doc.nsfp is None or not doc.nsfp.isdigit():
            continue
        assert len(doc.nsfp) == 17 and doc.nsfp.startswith("040026"), (sid, kind)
        if kind == "balasan" and seen.get(doc.nsfp) == sid:
            continue  # a corrected invoice keeps the original NSFP
        assert doc.nsfp not in seen, (sid, kind)
        seen[doc.nsfp] = sid
    assert built["PJK-02"].doc.nsfp is None
    assert built["PJK-03"].doc.nsfp.startswith("040.002-26.")
    assert len(re.sub(r"\D", "", built["PJK-03"].doc.nsfp)) == 16
    for sid in ("PJK-02", "PJK-03"):
        reply = built[sid].reply_doc.nsfp
        assert reply.isdigit() and len(reply) == 17, sid
    for sid, b in built.items():
        if b.reply_doc is not None and sid not in ("PJK-02", "PJK-03"):
            assert b.reply_doc.nsfp == b.doc.nsfp, sid


def test_substitute_identifiers(ds, built) -> None:
    npwp = built["PJK-05"].doc.seller.npwp
    assert stdnum_npwp.is_valid(npwp) and npwp not in dataset_npwps(ds)
    assert built["PJK-01"].doc.seller.npwp is None
    assert built["PJK-01"].reply_doc.seller.npwp == ds.vendor("v01").npwp

    accounts = {sid: built[sid].doc.bank for sid in ("REK-01", "REK-02", "REK-03", "INJ-04")}
    numbers = [a.number for a in accounts.values()]
    assert len(set(numbers)) == 4 and not set(numbers) & dataset_accounts(ds)
    bank_of = {b.name: b.key for b in ds.banks}
    assert bank_of[accounts["REK-01"].bank] == ds.vendor("v06").bank_account.bank
    assert bank_of[accounts["REK-02"].bank] != ds.vendor("v01").bank_account.bank
    assert accounts["REK-03"].bank == "Bank Kirana Digital"
    assert bank_of[accounts["INJ-04"].bank] != ds.vendor("v04").bank_account.bank

    rek04 = built["REK-04"].doc.bank.number
    assert rek04 != ds.vendor("v07").bank_account.number
    assert re.sub(r"\D", "", rek04) == ds.vendor("v07").bank_account.number
    assert built["REK-05"].doc.bank.number == ds.vendor("v05").bank_account.number
    note = built["REK-03"].doc.notes[0]
    assert accounts["REK-03"].number in note and "Bank Kirana Digital" in note


def test_printed_po_references(built) -> None:
    assert built["NPO-01"].doc.po_ref == "P00330"
    assert built["NPO-02"].doc.po_ref is None
    assert built["NPO-03"].doc.po_ref == "P00013"
    assert built["NPO-04"].doc.po_ref == "P00037"
    assert built["NPO-05"].doc.po_ref is None
    for sid, b in built.items():
        if sid[:3] != "NPO":
            assert b.doc.po_ref == b.scenario.po, sid


def test_emails(built) -> None:
    message_ids = set()
    for sid, b in built.items():
        if b.scenario.channel == "unggahan":
            assert b.email is None, sid
            continue
        e, seller = b.email, b.doc.seller
        assert e.sender == f"Bagian Penagihan {seller.name} <{seller.email}>", sid
        assert e.to == "ap@arunika.test", sid
        assert e.subject == f"Faktur {b.doc.number} – {seller.name}", sid
        day, hour = e.sent_at[:10], int(e.sent_at[11:13])
        assert day == b.doc.issue_date.isoformat() and 8 <= hour < 16, sid
        assert e.sent_at.endswith("+07:00"), sid
        message_ids.add(e.message_id)
    assert len(message_ids) == 20
    assert built["NPO-05"].email.sender.endswith("<tagihan@mitrapaletbaru.test>")


def generated_texts(built):
    for sid, b in built.items():
        for doc in filter(None, (b.doc, b.reply_doc)):
            yield sid, "catatan", " ".join(doc.notes + doc.hidden_texts + (doc.photo_note or "",))
            yield sid, "baris", " ".join(line.description for line in doc.lines)
        if b.reply_text:
            yield sid, "balasan", b.reply_text
        if b.email:
            yield sid, "email", f"{b.email.subject}\n{b.email.body}\n{b.email.message_id}"


def test_generated_texts_do_not_leak_labels(built) -> None:
    for sid, where, text in generated_texts(built):
        assert not LABEL_PATTERN.search(text), (sid, where)
        for key in CATEGORY_KEYS:
            assert not re.search(rf"\b{key}\b", text, re.IGNORECASE), (sid, where, key)


def test_reply_texts_are_polite_data(built) -> None:
    for sid, b in built.items():
        if b.reply_text:
            assert b.reply_text.startswith("Terima kasih"), sid
            assert b.doc.number in b.reply_text, sid
            assert "sistem" not in b.reply_text.lower(), sid
