"""BR-16 route priority and BR-17 allowed routes and guard."""

import pytest

from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.rules.routing import (
    RouteChoice,
    RouteRejected,
    allowed_routes,
    default_route,
    guard_route,
    initial_status,
    priority_route,
)

R = ReasonCode


@pytest.mark.aturan("BR-16")
@pytest.mark.parametrize(
    ("reasons", "route"),
    [
        ((), Route.A),
        ((R.PRICE_VARIANCE,), Route.B),
        ((R.PRICE_VARIANCE, R.TAX_INVALID), Route.B),
        ((R.PRICE_VARIANCE, R.PROMPT_INJECTION), Route.C),
        ((R.NEW_VENDOR,), Route.C),
    ],
)
def test_priority_c_over_b_over_a(reasons, route) -> None:
    assert priority_route(reasons) == route
    assert default_route(allowed_routes(reasons)) == route


@pytest.mark.aturan("BR-17")
@pytest.mark.parametrize(
    ("reasons", "allowed"),
    [
        ((), (Route.A,)),
        ((R.DUPLICATE_CONFIRMED,), (Route.B,)),
        ((R.DUPLICATE_CONFIRMED, R.QUANTITY_VARIANCE), (Route.B,)),
        ((R.QUANTITY_VARIANCE,), (Route.B, Route.C)),
        ((R.ARITHMETIC_ERROR, R.PO_UNRESOLVED), (Route.B, Route.C)),
        ((R.DUPLICATE_SUSPECTED,), (Route.C,)),
        ((R.DUPLICATE_CONFIRMED, R.OVER_THRESHOLD), (Route.C,)),
    ],
)
def test_allowed_sets(reasons, allowed) -> None:
    assert allowed_routes(reasons) == allowed


@pytest.mark.aturan("BR-17")
def test_guard_accepts_the_default() -> None:
    assert guard_route((Route.B, Route.C), RouteChoice(route=Route.B)) == Route.B


@pytest.mark.aturan("BR-17")
def test_guard_accepts_escalation_with_reason_and_quote() -> None:
    choice = RouteChoice(
        route=Route.C,
        justification="Vendor ini sering mengubah harga; perlu keputusan manajer.",
        policy_quotes=("Selisih berulang dari vendor yang sama dieskalasikan ke approver.",),
    )
    assert guard_route((Route.B, Route.C), choice) == Route.C


@pytest.mark.aturan("BR-17")
@pytest.mark.parametrize(
    ("allowed", "choice"),
    [
        ((Route.B, Route.C), RouteChoice(route=Route.A)),  # an LLM cannot force A
        ((Route.C,), RouteChoice(route=Route.B, justification="x", policy_quotes=("y",))),
        ((Route.B,), RouteChoice(route=Route.C, justification="x", policy_quotes=("y",))),
        ((Route.B, Route.C), RouteChoice(route=Route.C, justification="cukup alasan saja")),
        ((Route.B, Route.C), RouteChoice(route=Route.C, policy_quotes=("kutipan saja",))),
        ((Route.B, Route.C), RouteChoice(route=Route.C, justification=" ", policy_quotes=(" ",))),
    ],
)
def test_guard_refuses(allowed, choice) -> None:
    with pytest.raises(RouteRejected) as exc:
        guard_route(allowed, choice)
    assert exc.value.choice == choice and exc.value.allowed == allowed


@pytest.mark.aturan("BR-16")
def test_initial_status_per_route() -> None:
    assert initial_status(Route.A, ()) == InvoiceStatus.DRAFT_CREATED
    assert initial_status(Route.B, (R.PRICE_VARIANCE,)) == InvoiceStatus.AWAITING_VENDOR
    assert initial_status(Route.B, (R.DUPLICATE_CONFIRMED,)) == InvoiceStatus.CLOSED_DUPLICATE
    assert initial_status(Route.C, (R.NEW_VENDOR,)) == InvoiceStatus.AWAITING_APPROVAL


def test_empty_set_has_no_default() -> None:
    with pytest.raises(ValueError):
        default_route(())
