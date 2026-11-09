"""``npm run db:migrate``, ``npm run db:reset``, and ``python -m faktur_beres.db periksa``."""

import argparse
import sys
from collections.abc import Sequence

from alembic.util.exc import CommandError

from faktur_beres.config import get_settings
from faktur_beres.db.connection import DatabaseNotConfigured, app_url, database_name
from faktur_beres.db.migrate import check, reset, upgrade


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="faktur_beres.db", description=__doc__)
    parser.add_argument("perintah", choices=("migrasi", "reset", "periksa"))
    args = parser.parse_args(argv)
    try:
        url = app_url(get_settings())
    except DatabaseNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    name = database_name(url)
    if args.perintah == "migrasi":
        upgrade(url)
        print(f"Migrasi selesai di database {name}: Alembic head dan tabel checkpointer.")
        return 0
    if args.perintah == "reset":
        try:
            tables = reset(url)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not tables:
            print(f"Database {name} belum punya tabel. Jalankan npm run db:migrate dulu.")
            return 1
        print(f"Database {name} dikosongkan ({len(tables)} tabel). Skema tetap.")
        return 0
    try:
        check(url)
    except CommandError as exc:
        print(f"Model berbeda dari migrasi: {exc}", file=sys.stderr)
        return 1
    print(f"Database {name} sesuai dengan model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
