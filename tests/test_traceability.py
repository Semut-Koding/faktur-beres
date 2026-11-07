"""Every business rule computed by code in M2 has unit tests (ADR-0005, M2.4)."""

import re

from faktur_beres.domain.rules import CODE_RULES
from rule_marks import rule_tests


def test_every_code_rule_has_a_marked_test() -> None:
    index = rule_tests()
    missing = [rule for rule in CODE_RULES if not index.get(rule)]
    assert missing == []


def test_marks_name_known_rules_only() -> None:
    unknown = sorted(set(rule_tests()) - set(CODE_RULES))
    assert unknown == []


def test_code_rules_are_well_formed_and_unique() -> None:
    assert all(re.fullmatch(r"BR-\d{2}", r) for r in CODE_RULES)
    assert len(set(CODE_RULES)) == len(CODE_RULES)
    assert list(CODE_RULES) == sorted(CODE_RULES)
