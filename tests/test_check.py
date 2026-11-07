"""Unit tests for odoo:check logic that do not need a running Odoo."""

from typing import Any

import pytest

from faktur_beres.dataset import load_dataset
from faktur_beres.seed import check
from faktur_beres.seed.check import FAIL, INFO, LABEL_PATTERN, OK, Checker


class FakeOdoo:
    """Answers just enough JSON-2 calls for the checks under test."""

    def __init__(self, *, po_330: int = 0, next_po: int = 100, texts: dict | None = None) -> None:
        self.po_330 = po_330
        self.next_po = next_po
        self.texts = texts or {}

    def search_read(self, model: str, domain: list, fields: list, **kwargs: Any) -> list[dict]:
        if model == "ir.model.data":
            return []
        if model == "ir.sequence":
            return [{"id": 1, "prefix": "P", "padding": 5, "number_next_actual": self.next_po}]
        raise AssertionError(model)

    def search_count(self, model: str, domain: list) -> int:
        assert model == "purchase.order"
        return self.po_330

    def fields_get(self, model: str, attributes: list) -> dict:
        return {"name": {}, "ref": {}}

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        return [{"id": i, **self.texts.get(i, {})} for i in ids]


@pytest.fixture(scope="module")
def ds():
    return load_dataset()


def statuses(checker: Checker) -> list[str]:
    return [status for _, _, status in checker.rows]


def test_numbers_pass(ds) -> None:
    checker = Checker(FakeOdoo(), None, ds)
    checker.check_numbers()
    assert statuses(checker) == [OK, OK]
    assert not checker.failed


def test_existing_mistyped_po_and_low_sequence_fail(ds) -> None:
    checker = Checker(FakeOdoo(po_330=1, next_po=57), None, ds)
    checker.check_numbers()
    assert statuses(checker) == [FAIL, FAIL]
    assert checker.failed
    assert checker.rows[1][1] == "P00057"


def test_label_scan_finds_scenario_ids(ds) -> None:
    fake = FakeOdoo(texts={7: {"name": "TAGIH/1", "ref": "lihat QTY-01"}, 8: {"name": "ok"}})
    checker = Checker(fake, None, ds)
    hits = checker._scan("account.move", [7, 8])
    assert hits == ["account.move#7.ref: QTY-01"]


def test_info_rows_do_not_fail(ds) -> None:
    checker = Checker(FakeOdoo(), None, ds)
    checker.row("hak agent", "boleh unlink", None)
    assert statuses(checker) == [INFO]
    assert not checker.failed


def test_main_exit_code_on_failure(ds, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    class FakeClientCM(FakeOdoo):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_run(self: Checker) -> None:
        self.row("contoh", "gagal", False)

    monkeypatch.setattr(check, "admin_client", lambda settings=None: FakeClientCM())
    monkeypatch.setattr(Checker, "run", fake_run)
    monkeypatch.setattr(check.Settings, "__init__", lambda self, **kw: None, raising=False)
    monkeypatch.setattr(check, "Settings", type("S", (), {"odoo_agent_api_key": None}))

    assert check.main() == 1
    assert "GAGAL" in capsys.readouterr().out


def test_label_pattern_matches_catalogue_ids() -> None:
    assert LABEL_PATTERN.search("INJ-05")
    assert not LABEL_PATTERN.search("P00027 dan WH/IN/00026")


def test_judge_operations_requires_the_exact_set() -> None:
    assert check.judge_operations(["read"], ["read"]) == ("baca; tidak: buat, ubah, hapus", True)
    text, ok = check.judge_operations(["read", "write"], ["read"])
    assert (text, ok) == ("baca, ubah; tidak: buat, hapus", False)
    assert check.judge_operations([], ["read"])[1] is False


def test_agent_matrix_covers_every_model_of_the_task() -> None:
    # M2.1 matrix: the agent only reads master data and only writes draft vendor bills.
    read_only = {m for m, ops in check.AGENT_ACL.items() if ops == check.READ_ONLY}
    assert {"res.partner", "res.partner.bank", "purchase.order", "stock.picking"} <= read_only
    assert check.AGENT_ACL["account.move"] == ("read", "create", "write")
    assert set(check.AGENT_RULE_CLOSED) == {
        m for m, ops in check.AGENT_ACL.items() if ops != check.READ_ONLY
    }
    assert "account.group_account_invoice" in check.FORBIDDEN_AGENT_GROUPS


def test_manifest_version_is_read_from_the_repository() -> None:
    assert check.manifest_version() == "19.0.1.0.0"
