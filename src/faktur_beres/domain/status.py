"""SPEC §6.3: which status may follow which. The graph changes status only through this table."""

from faktur_beres.domain.reasons import InvoiceStatus as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.RECEIVED: frozenset({S.PROCESSING}),
    S.PROCESSING: frozenset(
        {
            S.AWAITING_VENDOR,
            S.AWAITING_APPROVAL,
            S.DRAFT_CREATED,
            S.CLOSED_DUPLICATE,
            S.REJECTED,
            S.FAILED,
        }
    ),
    S.AWAITING_VENDOR: frozenset({S.PROCESSING}),
    S.AWAITING_APPROVAL: frozenset({S.PROCESSING}),
    S.FAILED: frozenset({S.PROCESSING}),
    S.DRAFT_CREATED: frozenset(),
    S.CLOSED_DUPLICATE: frozenset(),
    S.REJECTED: frozenset(),
}


class InvalidTransition(ValueError):
    def __init__(self, current: S, new: S) -> None:
        super().__init__(f"status {current.value} tidak bisa berubah menjadi {new.value}")
        self.current = current
        self.new = new


def check_transition(current: S, new: S) -> None:
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(current, new)
