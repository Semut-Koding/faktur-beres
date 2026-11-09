"""The diagram document follows the graph; the CLI refuses paths it must not read."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from faktur_beres.config import REPO_ROOT
from faktur_beres.db.models import AuditEntry
from faktur_beres.graph.cli import PathRefused, checked_file, trace_line
from faktur_beres.graph.diagram import DOCUMENT, document, mermaid


def test_the_document_is_the_compiled_graph() -> None:
    assert DOCUMENT.read_text(encoding="utf-8") == document()


def test_the_graph_has_every_node_of_the_design() -> None:
    text = mermaid()
    for name in (
        "intake",
        "extract",
        "validate",
        "identify",
        "match",
        "decide",
        "act_bill",
        "act_vendor_email",
        "prepare_approval",
        "await_approval",
        "apply_decision",
        "report",
    ):
        assert name in text


@pytest.mark.parametrize("part", ["_privat", "privat", "PRIVAT"])
def test_a_private_folder_is_refused_without_being_read(tmp_path: Path, part: str) -> None:
    folder = REPO_ROOT / "var" / part
    with pytest.raises(PathRefused, match="privat"):
        checked_file(str(folder / "faktur.pdf"))
    assert not folder.exists() or part


def test_a_file_outside_the_repository_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "faktur.pdf"
    outside.write_bytes(b"%PDF")
    with pytest.raises(PathRefused, match="di dalam repo"):
        checked_file(str(outside))


def test_a_repository_file_is_accepted() -> None:
    assert checked_file(str(REPO_ROOT / "README.md")) == REPO_ROOT / "README.md"


def test_trace_line_masks_parameters() -> None:
    entry = AuditEntry(
        at=datetime(2026, 11, 10, 1, 2, 3, tzinfo=UTC),
        kind="tool",
        name="vendors_by_vat",
        node="identify",
        params={"npwp": "0990427957220000"},
        summary="1 baris",
        duration_ms=5,
        status="ok",
    )
    line = trace_line(entry)
    assert "0990********0000" in line and "0990427957220000" not in line
