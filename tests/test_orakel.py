"""Oracle evaluation, offline mode: the rules of SPEC 0.3 reproduce all 40 labels."""

import pytest

from faktur_beres.dataset import load_dataset
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.reasons import Route
from faktur_beres.evals.labels import LABEL_DIR, load_labels
from faktur_beres.evals.orakel import Simulator, offline_reader, run_oracle, score

pytestmark = pytest.mark.skipif(not LABEL_DIR.is_dir(), reason="eval/ belum dibuat")


@pytest.fixture(scope="module")
def labels():
    return load_labels()


@pytest.fixture(scope="module")
def simulator() -> Simulator:
    return Simulator(offline_reader(), load_policy(), load_dataset())


def test_offline_oracle_passes_all_forty(labels) -> None:
    report = run_oracle(offline_reader(), "luring", labels)
    failures = {
        s.id: [f"{c.name}: {c.detail}" for c in s.checks if not c.ok]
        for s in report.scores
        if not s.passed
    }
    assert failures == {}
    assert (report.passed, report.total) == (40, 40)


def test_expectations_are_not_inputs(labels, simulator: Simulator) -> None:
    for sid in ("COC-01", "QTY-01", "NPO-03", "DUP-01", "INJ-04", "NPO-05"):
        label = labels[sid]
        other = label.model_copy(
            update={
                "kategori": "cocok",
                "varian": "diubah",
                "vendor": "v99",
                "harapan": label.harapan.model_copy(
                    update={"jalur_awal": "A", "alasan_wajib": [], "tagihan_odoo": None}
                ),
            }
        )
        assert simulator.run(other) == simulator.run(label), sid


def test_scoring_notices_a_wrong_outcome(labels, simulator: Simulator) -> None:
    label = labels["QTY-01"]
    outcome = simulator.run(label)
    wrong = outcome.model_copy(update={"initial_route": Route.A, "bills": ()})
    result = score(label, wrong, load_policy())
    assert not result.passed
    assert {c.name for c in result.checks if not c.ok} == {"jalur_awal", "tagihan_odoo"}


def test_reply_po_numbers_keep_existing_open_pos_of_the_vendor(
    labels, simulator: Simulator
) -> None:
    reader = simulator.reader
    v07 = reader.vendors_by_name("PT Lapis Kimia Prima")[0]
    text = "PO P00013 sudah batal, gantinya PO P00016; faktur LKP-2610-0101. Lihat juga P00037."
    assert simulator.reply_po_numbers(text, v07.id) == ["P00016"]


def test_reply_from_another_address_is_ignored(labels, simulator: Simulator) -> None:
    label = labels["QTY-01"]
    reply = label.simulasi.balasan_vendor.model_copy(update={"dari": "lain@pemasok-lain.test"})
    forged = label.model_copy(
        update={"simulasi": label.simulasi.model_copy(update={"balasan_vendor": reply})}
    )
    outcome = simulator.run(forged)
    assert outcome.final_status.value == "awaiting_vendor" and outcome.bills == ()
    assert any("BR-20" in step for step in outcome.steps)


def test_seed_state_check_wants_only_the_history_bills() -> None:
    from fake_odoo import FakeOdoo, gateway
    from faktur_beres.evals.orakel import seed_state_problems

    ds = load_dataset()
    history = [
        {"id": i, "ref": bill.vendor_invoice_number, "state": "posted"}
        for i, (_, bill) in enumerate(ds.history_bills, start=1)
    ]
    assert seed_state_problems(gateway(FakeOdoo({"account.move/search_read": history})), ds) == []
    extra = [*history, {"id": 99, "ref": "KSM/INV/2026/10/0458", "state": "draft"}]
    problems = seed_state_problems(gateway(FakeOdoo({"account.move/search_read": extra})), ds)
    assert len(problems) == 2


def test_modes_are_compared_without_ids(labels) -> None:
    from faktur_beres.evals.orakel import compare_reports

    report = run_oracle(offline_reader(), "luring", labels)
    assert compare_reports(report, report) == []
    changed = report.model_copy(
        update={
            "outcomes": tuple(
                o.model_copy(update={"final_status": o.initial_status}) if o.id == "QTY-01" else o
                for o in report.outcomes
            )
        }
    )
    assert compare_reports(report, changed) == ["QTY-01"]
