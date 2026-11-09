"""Helpers for the integration tests: analyse a label against Odoo and name things neutrally."""

import hashlib
from pathlib import Path

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.engine import AnalysisResult, analyse
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.rules.safety import idempotency_key
from faktur_beres.evals.labels import (
    email_fragments,
    label_intake,
    label_invoice_id,
    truth_fragments,
    truth_to_invoice,
)
from faktur_beres.generator.label_schema import Label
from faktur_beres.tools.odoo_tools import OdooErpReader

POLICY = load_policy()
MIMETYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".xml": "application/xml",
}


def analyse_label(label: Label, erp: OdooErpReader) -> AnalysisResult:
    truth = label.kebenaran_dokumen
    fragments = truth_fragments(truth, document_format=label.format) + email_fragments(label)
    return analyse(truth_to_invoice(truth), label_intake(label), fragments, erp, POLICY)


def invoice_id(label: Label) -> str:
    """A neutral invoice id from the file hash: nothing in Odoo may name the scenario."""
    return label_invoice_id(label)


def keys(label: Label, bill_target: str) -> dict[str, str]:
    """BR-22 keys of the three actions for one invoice."""
    iid = invoice_id(label)
    return {
        action: idempotency_key(iid, action, bill_target, label.berkas.sha256)
        for action in ("create_draft_bill", "attach_file", "post_note")
    }


def original_file(label: Label) -> tuple[str, bytes, str]:
    path = REPO_ROOT / label.berkas.path
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == label.berkas.sha256
    return label.berkas.nama_kirim, data, MIMETYPES[Path(path).suffix]
