"""Oracle cross-check of the robustness set: the rules on the truth give the expected route."""

import pytest

from faktur_beres.dataset.loader import load_dataset
from faktur_beres.domain.policy import load_policy
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.ketahanan import cross_check
from faktur_beres.evals.labels import load_robustness_labels
from faktur_beres.generator.label_schema import RobustnessLabel

LABELS = load_robustness_labels()
READER = DatasetErpReader(load_dataset())
POLICY = load_policy()


@pytest.mark.parametrize("label", LABELS.values(), ids=lambda label: label.id)
def test_rules_on_the_truth_match_the_expectation(label: RobustnessLabel) -> None:
    check = cross_check(label, READER, POLICY)
    assert check.ok, check.problems


def test_at_most_two_documents_wait_for_m4() -> None:
    waiting = [label.id for label in LABELS.values() if label.harapan.menunggu]
    assert len(waiting) <= 2
    for document in waiting:
        assert cross_check(LABELS[document], READER, POLICY).waiting == "M4"
