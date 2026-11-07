"""Reason codes, routes, and invoice statuses (SPEC §6).

Enum values are the codes used in labels, the application database, and the dashboard.
"""

from enum import StrEnum


class Route(StrEnum):
    """Routes in priority order A < B < C (BR-16)."""

    A = "A"
    B = "B"
    C = "C"

    @property
    def code(self) -> str:
        return _ROUTE_CODES[self]

    @property
    def rank(self) -> int:
        return ROUTE_ORDER.index(self)


ROUTE_ORDER: tuple[Route, ...] = (Route.A, Route.B, Route.C)
_ROUTE_CODES = {Route.A: "auto_draft", Route.B: "vendor_query", Route.C: "needs_approval"}


class ReasonCode(StrEnum):
    """B1–B6 lead to route B, C1–C7 to route C (SPEC §6.2)."""

    PRICE_VARIANCE = "price_variance"
    QUANTITY_VARIANCE = "quantity_variance"
    TAX_INVALID = "tax_invalid"
    PO_UNRESOLVED = "po_unresolved"
    DUPLICATE_CONFIRMED = "duplicate_confirmed"
    ARITHMETIC_ERROR = "arithmetic_error"
    OVER_THRESHOLD = "over_threshold"
    NEW_VENDOR = "new_vendor"
    BANK_ACCOUNT_MISMATCH = "bank_account_mismatch"
    VALIDATION_FAILED = "validation_failed"
    PROMPT_INJECTION = "prompt_injection"
    DUPLICATE_SUSPECTED = "duplicate_suspected"
    IDENTITY_MISMATCH = "identity_mismatch"

    @property
    def number(self) -> str:
        """The SPEC §6.2 number, e.g. ``B1`` or ``C7``."""
        return _NUMBERS[self]

    @property
    def route(self) -> Route:
        return Route(self.number[0])


_NUMBERS: dict[ReasonCode, str] = {
    code: f"{group}{i}"
    for group, codes in (
        ("B", list(ReasonCode)[:6]),
        ("C", list(ReasonCode)[6:]),
    )
    for i, code in enumerate(codes, start=1)
}


class InvoiceStatus(StrEnum):
    """Invoice statuses (SPEC §6.3)."""

    RECEIVED = "received"
    PROCESSING = "processing"
    AWAITING_VENDOR = "awaiting_vendor"
    AWAITING_APPROVAL = "awaiting_approval"
    DRAFT_CREATED = "draft_created"
    CLOSED_DUPLICATE = "closed_duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
