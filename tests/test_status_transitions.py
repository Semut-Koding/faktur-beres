"""SPEC §6.3: the status table."""

import pytest

from faktur_beres.domain.reasons import InvoiceStatus as S
from faktur_beres.domain.status import TRANSITIONS, InvalidTransition, check_transition


def test_every_status_has_a_row() -> None:
    assert set(TRANSITIONS) == set(S)


@pytest.mark.parametrize("final", [S.DRAFT_CREATED, S.CLOSED_DUPLICATE, S.REJECTED])
def test_final_statuses_lead_nowhere(final: S) -> None:
    assert TRANSITIONS[final] == frozenset()
    with pytest.raises(InvalidTransition):
        check_transition(final, S.PROCESSING)


@pytest.mark.parametrize("waiting", [S.AWAITING_VENDOR, S.AWAITING_APPROVAL, S.FAILED])
def test_waiting_statuses_only_go_back_to_processing(waiting: S) -> None:
    assert TRANSITIONS[waiting] == frozenset({S.PROCESSING})


def test_received_only_goes_to_processing() -> None:
    check_transition(S.RECEIVED, S.PROCESSING)
    with pytest.raises(InvalidTransition):
        check_transition(S.RECEIVED, S.DRAFT_CREATED)


def test_processing_reaches_every_stop() -> None:
    for stop in (S.AWAITING_VENDOR, S.AWAITING_APPROVAL, S.DRAFT_CREATED, S.FAILED, S.REJECTED):
        check_transition(S.PROCESSING, stop)
