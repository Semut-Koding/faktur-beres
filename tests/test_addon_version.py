"""The addon's content is locked to its version (M3.1): no change without a version bump."""

import json
import shutil
from pathlib import Path

import pytest

from faktur_beres.devtools.addon import (
    FINGERPRINT_PATH,
    UPDATE_COMMAND,
    compare_versions,
    fingerprint,
    manifest_version,
    snapshot_warning,
    version_tuple,
)


def test_addon_content_matches_the_fingerprint_of_its_version() -> None:
    recorded = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8"))
    version = manifest_version()
    if fingerprint() != recorded["sha256"]:
        if version == recorded["version"]:
            pytest.fail(
                f"isi addon berubah tetapi versinya tetap {version}: naikkan versi di "
                f"__manifest__.py, lalu perbarui sidik dengan `{UPDATE_COMMAND}`"
            )
        pytest.fail(f"versi addon naik ke {version}; perbarui sidik dengan `{UPDATE_COMMAND}`")
    assert version == recorded["version"]


def test_fingerprint_ignores_bytecode_and_sees_every_change(tmp_path: Path) -> None:
    from faktur_beres.devtools.addon import ADDON_DIR

    copy = tmp_path / "addon"
    shutil.copytree(ADDON_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    base = fingerprint(copy)
    assert base == fingerprint()
    (copy / "models" / "__pycache__").mkdir()
    (copy / "models" / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\0")
    assert fingerprint(copy) == base
    rule_file = copy / "security" / "ir.model.access.csv"
    rule_file.write_text(rule_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert fingerprint(copy) != base


def test_versions_compare_as_numbers() -> None:
    assert version_tuple("19.0.1.0.0") == (19, 0, 1, 0, 0)
    assert compare_versions("19.0.1.0.0", "19.0.1.0.0") == "sama"
    assert compare_versions("19.0.1.0.10", "19.0.1.0.9") == "lebih_baru"  # not a text compare
    assert compare_versions("19.0.1.0.0", "19.0.2.0.0") == "lebih_lama"
    with pytest.raises(ValueError):
        version_tuple("19.0.x")


def test_reset_warns_about_an_older_snapshot() -> None:
    assert snapshot_warning("19.0.1.0.0", "19.0.1.0.0") is None
    assert snapshot_warning("19.0.1.0.1", "19.0.1.0.0") is None
    warning = snapshot_warning("19.0.1.0.0", "19.0.1.1.0")
    assert warning is not None
    assert "snapshot memakai addon 19.0.1.0.0, repo 19.0.1.1.0" in warning
    assert "npm run odoo:init lalu npm run odoo:snapshot" in warning
    assert "tidak terpasang" in (snapshot_warning(None, "19.0.1.0.0") or "")
