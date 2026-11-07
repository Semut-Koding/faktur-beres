"""Second half of ``npm run odoo:init`` (after database and modules exist).

Steps, all safe to repeat:

1. company data from the dataset, time zone, base URL, and the purchase tax (ORM script);
2. admin password, the agent bot user (no password; exactly the dedicated group
   ``faktur_beres_agent.group_agent`` plus inbox notifications), and the admin and agent API keys
   (ORM script). New keys go straight into ``.env`` and are never printed;
3. smoke test: the agent key can call ``res.partner/search_read`` over JSON-2.
"""

import sys

from faktur_beres.config import ENV_FILE, Settings
from faktur_beres.dataset import Dataset, get_dataset
from faktur_beres.dataset.money import ppn_amount
from faktur_beres.devtools.envfile import read_env, set_env_values
from faktur_beres.odoo import OdooClient, OdooError
from faktur_beres.seed.odoo_shell import OdooShellError, run_script
from faktur_beres.textui import print_table

BASE_URL = "http://localhost:28069"
TIME_ZONE = "Asia/Jakarta"
CHART_TEMPLATE = "id"
TAX_XMLID = "fb_seed.tax_ppn_masukan"
# l10n_id purchase tax "12% (Non-Luxury Good)": 11% of the price, i.e. 12% x DPP nilai lain 11/12.
LOCALIZATION_TAX_TEMPLATE = "tax_PT4"

AGENT_XMLID = "fb_seed.user_agen"
# The dedicated group of the faktur_beres_agent addon (infra/odoo/addons): read ERP data, create and
# edit the agent's own draft vendor bills, attachments, and notes; no delete, no posting. The ORM
# script adds mail.group_mail_notification_type_inbox and nothing else (RENCANA-TEKNIS §4.4).
AGENT_GROUPS = ("faktur_beres_agent.group_agent",)
API_KEY_DAYS = 90
API_KEY_SCOPE = "rpc"
ENV_KEYS = {"admin": "ODOO_ADMIN_API_KEY", "agent": "ODOO_AGENT_API_KEY"}


def rupiah(amount: float) -> str:
    return f"Rp{amount:,.0f}".replace(",", ".")


def company_params(ds: Dataset) -> dict:
    c = ds.company
    return {
        "company": {
            "name": c.name,
            "npwp": c.npwp,
            "street": c.street,
            "city": c.city,
            "state": c.state,
            "zip": c.zip,
            "country": c.country,
            "currency": c.currency,
            "email": c.email_ap,
            "website": f"https://{c.website}",
        },
        "tz": TIME_ZONE,
        "chart_template": CHART_TEMPLATE,
        "base_url": BASE_URL,
        "tax": {
            "xmlid": TAX_XMLID,
            "localization_template": LOCALIZATION_TAX_TEMPLATE,
            "ppn_on_million": ppn_amount(1_000_000, ds.purchase_tax.effective_rate_percent),
        },
    }


def init_company(ds: Dataset) -> dict:
    print("== Perusahaan, pajak pembelian, dan URL dasar")
    result = run_script("company.py", company_params(ds))
    print_table(("Langkah", "Hasil"), result["steps"])
    tax = result["tax"]
    source = ", ".join(tax["source_xmlids"]) or "buatan"
    print(
        f"  pajak {TAX_XMLID} -> {tax['name']} ({source}); "
        f"PPN untuk Rp1.000.000 = {rupiah(tax['ppn_on_million'])}"
    )
    return result


def access_params(ds: Dataset) -> dict:
    agent = ds.user("agen")
    return {
        "scope": API_KEY_SCOPE,
        "key_days": API_KEY_DAYS,
        "admin_key_name": "faktur-beres admin (odoo:init)",
        "agent": {
            "xmlid": AGENT_XMLID,
            "login": agent.login,
            "name": agent.name,
            "groups": list(AGENT_GROUPS),
            "lang": "id_ID",
            "tz": TIME_ZONE,
            "key_name": "faktur-beres agent (odoo:init)",
        },
    }


def init_access(ds: Dataset) -> dict:
    """Ensure admin password, agent user, and API keys; write new keys to .env."""
    print("\n== Sandi admin, user agent, dan API key")
    env = read_env(ENV_FILE)
    secrets = {
        "FB_ADMIN_PASSWORD": env.get("ODOO_ADMIN_PASSWORD", ""),
        "FB_ADMIN_KEY": env.get(ENV_KEYS["admin"], ""),
        "FB_AGENT_KEY": env.get(ENV_KEYS["agent"], ""),
    }
    result = run_script("access.py", access_params(ds), secrets=secrets)
    new_keys = {ENV_KEYS[label]: key for label, key in result["keys"].items()}
    if new_keys:
        set_env_values(ENV_FILE, new_keys)
    print_table(("Langkah", "Hasil"), result["steps"])
    for name in sorted(new_keys):
        print(f"  {name} ditulis ke .env (nilai tidak ditampilkan)")
    return result


def smoke_test() -> None:
    """The agent key must be able to read through JSON-2."""
    settings = Settings()
    if settings.odoo_agent_api_key is None:
        raise OdooError("ODOO_AGENT_API_KEY kosong setelah init")
    with OdooClient(
        settings.odoo_url,
        settings.odoo_db,
        settings.odoo_agent_api_key,
        user_agent="faktur-beres-init/0.1",
    ) as agent:
        rows = agent.search_read("res.partner", [], ["id"], limit=1)
    print(f"\n== Uji asap key agent: res.partner/search_read limit 1 -> {len(rows)} baris, OK")


def main() -> int:
    ds = get_dataset()
    try:
        init_company(ds)
        init_access(ds)
        smoke_test()
    except (OdooShellError, OdooError) as exc:
        print(f"galat: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
