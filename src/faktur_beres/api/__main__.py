"""``npm run api``: the live API on 127.0.0.1:28000 (standard port 8000 + 20000)."""

import logging

import uvicorn

from faktur_beres.api.app import ApiConfig, create_app
from faktur_beres.api.guard import GuardedRunner
from faktur_beres.api.reads import DbReads
from faktur_beres.config import REPO_ROOT, get_settings
from faktur_beres.db.connection import app_url, make_engine
from faktur_beres.graph.factory import live_poller, live_runner
from faktur_beres.llm import usage


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()

    def secret(value: object) -> str | None:
        return value.get_secret_value() if value is not None else None  # type: ignore[attr-defined]

    config = ApiConfig(
        code_ap=secret(settings.demo_kode_ap),
        code_approver=secret(settings.demo_kode_approver),
        inbox_interval=settings.inbox_interval_detik,
        mailpit_public_url=settings.public_mailpit_url or settings.mailpit_url,
        odoo_public_url=settings.public_odoo_url or settings.odoo_url,
        static_dir=REPO_ROOT / "dashboard" / "dist",
        evaluation_summary=REPO_ROOT / "eval" / "ringkasan-terakhir.json",
    )
    handler = usage.attach("api")
    engine = make_engine(app_url(settings))
    try:
        with live_runner(settings) as inner:
            runner = GuardedRunner(inner)
            poller = live_poller(settings, runner) if config.inbox_interval else None
            app = create_app(runner, DbReads(engine), config, poller)
            uvicorn.run(
                app,
                host=settings.api_host,
                port=settings.api_port,
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
                log_level="info",
            )
    finally:
        usage.detach(handler)
        engine.dispose()


if __name__ == "__main__":
    main()
