"""Shared helpers for the seed and check scripts (developer tooling, admin API key only)."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from faktur_beres.config import Settings
from faktur_beres.odoo import OdooClient

MODULE = "fb_seed"
WIB = timezone(timedelta(hours=7))
WORK_HOUR = 10  # dataset dates become 10:00 WIB


class SeedError(RuntimeError):
    """The seed cannot continue (missing prerequisite or unexpected Odoo state)."""


def admin_client(settings: Settings | None = None) -> OdooClient:
    settings = settings or Settings()
    if settings.odoo_admin_api_key is None:
        raise SeedError("ODOO_ADMIN_API_KEY kosong; jalankan npm run odoo:init")
    return OdooClient(
        settings.odoo_url,
        settings.odoo_db,
        settings.odoo_admin_api_key,
        timeout=settings.odoo_timeout,
        user_agent="faktur-beres-seed/0.1",
    )


def odoo_datetime(day: date, hour: int = WORK_HOUR) -> str:
    """A dataset date as Odoo's naive-UTC datetime string (10:00 WIB = 03:00 UTC)."""
    local = datetime(day.year, day.month, day.day, hour, tzinfo=WIB)
    return local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def m2o_id(value: Any) -> int | None:
    """Normalize a many2one value read from Odoo (``[id, name]`` or ``False``)."""
    if isinstance(value, list | tuple):
        return value[0] if value else None
    return value or None


class XmlIds:
    """External IDs (``ir.model.data``) used to make the seed idempotent."""

    def __init__(self, client: OdooClient, module: str = MODULE) -> None:
        self.client = client
        self.module = module
        self._own: dict[str, tuple[str, int]] = {}
        self._other: dict[str, tuple[str, int]] = {}
        self.reload()

    def reload(self) -> None:
        rows = self.client.search_read(
            "ir.model.data", [["module", "=", self.module]], ["name", "model", "res_id"]
        )
        self._own = {r["name"]: (r["model"], r["res_id"]) for r in rows}

    def get(self, name: str, model: str) -> int | None:
        entry = self._own.get(name)
        if entry is None:
            return None
        if entry[0] != model:
            raise SeedError(f"{self.module}.{name} menunjuk {entry[0]}, bukan {model}")
        return entry[1]

    def register(self, name: str, model: str, res_id: int) -> None:
        self.client.create(
            "ir.model.data",
            {
                "module": self.module,
                "name": name,
                "model": model,
                "res_id": res_id,
                "noupdate": True,
            },
        )
        self._own[name] = (model, res_id)

    def names(self) -> dict[str, tuple[str, int]]:
        return dict(self._own)

    def ref(self, xmlid: str, model: str | None = None) -> int:
        """Resolve any ``module.name`` external ID."""
        if xmlid not in self._other:
            module, name = xmlid.split(".", 1)
            rows = self.client.search_read(
                "ir.model.data",
                [["module", "=", module], ["name", "=", name]],
                ["model", "res_id"],
                limit=1,
            )
            if not rows:
                raise SeedError(f"external ID {xmlid} tidak ditemukan")
            self._other[xmlid] = (rows[0]["model"], rows[0]["res_id"])
        found_model, res_id = self._other[xmlid]
        if model and found_model != model:
            raise SeedError(f"{xmlid} adalah {found_model}, bukan {model}")
        return res_id
