"""Run after ``odoo db duplicate`` in ``npm run odoo:reset``.

Duplicating a database makes Odoo regenerate a few system parameters in the copy
(``ir.config_parameter.init(force=True)``), which resets ``web.base.url`` to port 8069. This puts
the local URL back and checks that both API keys in ``.env`` still work against the restored data.
It also warns when the snapshot holds an older version of the addon than the repository.
"""

import sys

from faktur_beres.config import Settings
from faktur_beres.devtools.addon import ADDON, manifest_version, snapshot_warning
from faktur_beres.odoo import OdooAuthError, OdooClient, OdooError
from faktur_beres.seed.common import SeedError, admin_client
from faktur_beres.seed.init import BASE_URL


def main() -> int:
    settings = Settings()
    try:
        with admin_client(settings) as admin:
            rows = admin.search_read(
                "ir.module.module",
                [["name", "=", ADDON], ["state", "=", "installed"]],
                ["latest_version"],
            )
            warning = snapshot_warning(
                rows[0]["latest_version"] if rows else None, manifest_version()
            )
            if warning:
                print(f"  {warning}")
            current = admin.call("ir.config_parameter", "get_param", key="web.base.url")
            if current != BASE_URL:
                admin.call("ir.config_parameter", "set_param", key="web.base.url", value=BASE_URL)
                print(f"  web.base.url dipulihkan ke {BASE_URL}")
            if settings.odoo_agent_api_key is None:
                raise SeedError("ODOO_AGENT_API_KEY kosong; jalankan npm run odoo:init")
            with OdooClient(
                settings.odoo_url, settings.odoo_db, settings.odoo_agent_api_key
            ) as agent:
                agent.search_read("res.partner", [], ["id"], limit=1)
        print("  key admin dan agent berlaku")
    except OdooAuthError:
        print(
            "galat: API key di .env tidak berlaku pada data hasil reset. Jalankan npm run "
            "odoo:init, lalu npm run odoo:snapshot agar snapshot memuat key yang baru.",
            file=sys.stderr,
        )
        return 1
    except (SeedError, OdooError) as exc:
        print(f"galat: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
