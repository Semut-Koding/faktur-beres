"""Deterministic business rules (SPEC §8), one module per group, all pure functions over data.

Inputs are ``InvoiceData``, ERP records, and ``PolicyConfig``; outputs are typed ``Finding``
values with evidence. No rule reads the ERP, a file, or the clock itself.
"""

#: The rules computed by code (M2, M3). Every one must have at least one unit test marked
#: ``@pytest.mark.aturan("BR-xx")`` (tests/test_traceability.py).
CODE_RULES: tuple[str, ...] = (
    "BR-02",
    "BR-03",
    "BR-05",
    "BR-06",
    "BR-07",
    "BR-08",
    "BR-09",
    "BR-10",
    "BR-11",
    "BR-12",
    "BR-13",
    "BR-14",
    "BR-15",
    "BR-16",
    "BR-17",
    "BR-18",
    "BR-22",
    "BR-23",
    "BR-24",
    "BR-25",
    "BR-27",
    "BR-28",
)
