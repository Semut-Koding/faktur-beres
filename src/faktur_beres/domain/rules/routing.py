"""BR-16 route priority and BR-17 allowed routes, with the guard on the route choice.

* BR-16: any C reason → C; otherwise any B reason → B; otherwise A. Route A only when every check
  passed; an LLM cannot force A.
* BR-17: the code computes the allowed set from the reasons: no reason → {A};
  ``duplicate_confirmed`` without a C reason → {B}; other B reasons without a C reason → {B, C};
  any C reason → {C}. The default is the lowest route in the set. A different route inside the
  set (e.g. escalating B to C) needs a written justification and a policy quote. A choice outside
  the set is refused by the guard; the caller records the refusal.
"""

from collections.abc import Iterable, Sequence

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.reasons import ROUTE_ORDER, InvoiceStatus, ReasonCode, Route

RULE_PRIORITY = "BR-16"
RULE_ALLOWED = "BR-17"


def priority_route(reasons: Iterable[ReasonCode]) -> Route:
    """BR-16: C > B > A."""
    codes = set(reasons)
    if any(r.route == Route.C for r in codes):
        return Route.C
    if codes:
        return Route.B
    return Route.A


def allowed_routes(reasons: Iterable[ReasonCode]) -> tuple[Route, ...]:
    """BR-17: the set of routes the decision may take, in route order."""
    codes = set(reasons)
    if any(r.route == Route.C for r in codes):
        return (Route.C,)
    if not codes:
        return (Route.A,)
    if ReasonCode.DUPLICATE_CONFIRMED in codes:
        return (Route.B,)
    return (Route.B, Route.C)


def default_route(allowed: Sequence[Route]) -> Route:
    """The lowest route of the allowed set."""
    if not allowed:
        raise ValueError("himpunan jalur kosong")
    return min(allowed, key=ROUTE_ORDER.index)


def initial_status(route: Route, reasons: Iterable[ReasonCode]) -> InvoiceStatus:
    """The status when the agent first stops on ``route`` (SPEC §6.1)."""
    if route == Route.A:
        return InvoiceStatus.DRAFT_CREATED
    if route == Route.B:
        if ReasonCode.DUPLICATE_CONFIRMED in set(reasons):
            return InvoiceStatus.CLOSED_DUPLICATE
        return InvoiceStatus.AWAITING_VENDOR
    return InvoiceStatus.AWAITING_APPROVAL


class RouteChoice(DomainModel):
    """A route chosen by the LLM (M4), with its written reason and policy quotes."""

    route: Route
    justification: str | None = None
    policy_quotes: tuple[str, ...] = ()


class RouteRejected(ValueError):
    """The guard refused a route choice (BR-17)."""

    def __init__(self, choice: RouteChoice, allowed: Sequence[Route], why: str) -> None:
        super().__init__(f"jalur {choice.route.value} ditolak: {why}")
        self.choice = choice
        self.allowed = tuple(allowed)
        self.why = why


def guard_route(allowed: Sequence[Route], choice: RouteChoice) -> Route:
    """Accept ``choice`` only inside ``allowed``; leaving the default needs reason and quote."""
    if choice.route not in allowed:
        names = ", ".join(r.value for r in allowed)
        raise RouteRejected(choice, allowed, f"di luar himpunan yang diizinkan {{{names}}}")
    if choice.route != default_route(allowed):
        has_reason = bool(choice.justification and choice.justification.strip())
        has_quote = any(q.strip() for q in choice.policy_quotes)
        if not (has_reason and has_quote):
            raise RouteRejected(
                choice,
                allowed,
                "pindah dari jalur bawaan butuh alasan tertulis dan kutipan kebijakan",
            )
    return choice.route
