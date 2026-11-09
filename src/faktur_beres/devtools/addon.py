"""Version and fingerprint of the Odoo addon ``faktur_beres_agent`` (developer tooling).

``npm run odoo:init`` upgrades the addon only when the version in ``__manifest__.py`` is newer
than the one installed in the database. Its content is locked by a fingerprint
(``tests/data/addon_fingerprint.json``), so a change without a version bump fails the tests.

    python -m faktur_beres.devtools.addon versi                # manifest version
    python -m faktur_beres.devtools.addon banding <versi-db>   # sama | lebih_baru | lebih_lama
    python -m faktur_beres.devtools.addon sidik [--tulis]      # fingerprint (and write it)
"""

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

from faktur_beres.config import REPO_ROOT

ADDON = "faktur_beres_agent"
ADDON_DIR = REPO_ROOT / "infra" / "odoo" / "addons" / ADDON
FINGERPRINT_PATH = REPO_ROOT / "tests" / "data" / "addon_fingerprint.json"
UPDATE_COMMAND = "uv run python -m faktur_beres.devtools.addon sidik --tulis"
Relation = Literal["sama", "lebih_baru", "lebih_lama"]


def manifest_version(addon_dir: Path = ADDON_DIR) -> str:
    manifest = ast.literal_eval((addon_dir / "__manifest__.py").read_text(encoding="utf-8"))
    return str(manifest["version"])


def version_tuple(version: str) -> tuple[int, ...]:
    """``19.0.1.0.0`` -> ``(19, 0, 1, 0, 0)``; anything but dotted numbers is an error."""
    parts = version.strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"versi addon tidak valid: {version!r}")
    return tuple(int(p) for p in parts)


def compare_versions(manifest: str, database: str) -> Relation:
    """The manifest version relative to the version installed in the database."""
    m, d = version_tuple(manifest), version_tuple(database)
    if m == d:
        return "sama"
    return "lebih_baru" if m > d else "lebih_lama"


def addon_files(addon_dir: Path = ADDON_DIR) -> list[Path]:
    return sorted(
        p
        for p in addon_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def fingerprint(addon_dir: Path = ADDON_DIR) -> str:
    """SHA-256 over the relative path and content of every addon file (without ``__pycache__``)."""
    digest = hashlib.sha256()
    for path in addon_files(addon_dir):
        digest.update(path.relative_to(addon_dir).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def snapshot_warning(database: str | None, manifest: str) -> str | None:
    """The ``odoo:reset`` warning when the restored database has an older (or no) addon."""
    if database and compare_versions(manifest, database) != "lebih_baru":
        return None
    used = database or "tidak terpasang"
    return (
        f"peringatan: snapshot memakai addon {used}, repo {manifest}: "
        "jalankan npm run odoo:init lalu npm run odoo:snapshot"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m faktur_beres.devtools.addon")
    sub = parser.add_subparsers(dest="perintah", required=True)
    sub.add_parser("versi", help="versi di __manifest__.py")
    banding = sub.add_parser("banding", help="bandingkan versi manifest dengan versi database")
    banding.add_argument("versi_db")
    sidik = sub.add_parser("sidik", help="sidik isi addon")
    sidik.add_argument("--tulis", action="store_true", help=f"tulis ke {FINGERPRINT_PATH.name}")
    args = parser.parse_args(argv)

    if args.perintah == "versi":
        print(manifest_version())
    elif args.perintah == "banding":
        try:
            print(compare_versions(manifest_version(), args.versi_db))
        except ValueError as exc:
            print(f"galat: {exc}", file=sys.stderr)
            return 2
    else:
        data = {"addon": ADDON, "version": manifest_version(), "sha256": fingerprint()}
        if args.tulis:
            FINGERPRINT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"sidik ditulis ke {FINGERPRINT_PATH.relative_to(REPO_ROOT)}")
        print(json.dumps(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
