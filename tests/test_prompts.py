"""Prompts: a version never changes once written, and no prompt carries labels or ERP data.

Every LLM-facing text is checked: the prompt files of every version, and the JSON schemas sent
with them (field descriptions included). Forbidden: scenario IDs, category keys and variants of the
labels; vendor names, NPWPs, account numbers and PO numbers of the dataset; the company's name and
NPWP.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset.loader import load_dataset
from faktur_beres.evals.labels import load_labels
from faktur_beres.extraction.schema import ExtractedInvoice
from faktur_beres.tools.injection_review import ReviewAnswer

PACKAGE_DIR = REPO_ROOT / "src" / "faktur_beres"
FINGERPRINTS = REPO_ROOT / "tests" / "data" / "prompt_fingerprints.json"
SCHEMAS = [ExtractedInvoice, ReviewAnswer]


def prompt_dirs() -> list[Path]:
    return sorted(p for p in PACKAGE_DIR.glob("*/prompts/*") if p.is_dir())


def fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.glob("*.txt")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def llm_facing_text() -> str:
    texts = [p.read_text(encoding="utf-8") for d in prompt_dirs() for p in sorted(d.glob("*.txt"))]
    texts += [json.dumps(schema.model_json_schema(), ensure_ascii=False) for schema in SCHEMAS]
    return "\n".join(texts)


def forbidden_values() -> list[str]:
    ds = load_dataset()
    values = [ds.company.name, ds.company.npwp]
    for vendor in ds.vendors:
        values += [vendor.name, vendor.npwp, vendor.bank_account.number, vendor.billing_email]
    values += [po.number for po in ds.purchase_orders]
    for scenario, label in load_labels().items():
        values += [scenario, label.kategori, label.varian]
    return values


def test_prompt_versions_have_known_fingerprints() -> None:
    known = json.loads(FINGERPRINTS.read_text(encoding="utf-8"))
    found = {folder.name: fingerprint(folder) for folder in prompt_dirs()}
    assert found, "tidak ada prompt"
    changed = [v for v, digest in found.items() if v in known and known[v] != digest]
    assert changed == [], (
        f"prompt {changed} berubah: buat direktori versi baru (mis. -v2), jangan sunting versi lama"
    )
    unknown = sorted(set(found) - set(known))
    assert unknown == [], f"versi baru {unknown}: tambahkan sidiknya ke {FINGERPRINTS.name}"


@pytest.mark.parametrize("folder", prompt_dirs(), ids=lambda p: p.name)
def test_version_names_follow_the_pattern(folder: Path) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9-]*-v[0-9]+", folder.name)


def test_no_label_or_erp_value_reaches_the_llm() -> None:
    text = llm_facing_text().casefold()
    digits = re.sub(r"\D", "", text)
    leaked = [v for v in forbidden_values() if v and v.casefold() in text]
    leaked += [
        v
        for v in forbidden_values()
        if len(re.sub(r"\D", "", v)) >= 8 and re.sub(r"\D", "", v) in digits
    ]
    assert leaked == []


def test_the_check_would_notice_a_leak() -> None:
    sample = forbidden_values()
    assert len(sample) > 100 and any(v.startswith("PT ") for v in sample)
