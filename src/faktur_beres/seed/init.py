"""Second half of ``npm run odoo:init`` (after database and modules exist).

Steps, all safe to repeat:

1. company data from the dataset, time zone, base URL, and the purchase tax (ORM script);
2. the agent bot user and the admin/agent API keys (added in the next step of M0.6).
"""

import sys

from faktur_beres.dataset import Dataset, get_dataset
from faktur_beres.dataset.money import ppn_amount
from faktur_beres.seed.odoo_shell import OdooShellError, run_script
from faktur_beres.textui import print_table

BASE_URL = "http://localhost:28069"
TIME_ZONE = "Asia/Jakarta"
CHART_TEMPLATE = "id"
TAX_XMLID = "fb_seed.tax_ppn_masukan"
# l10n_id purchase tax "12% (Non-Luxury Good)": 11% of the price, i.e. 12% x DPP nilai lain 11/12.
LOCALIZATION_TAX_TEMPLATE = "tax_PT4"


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
    print(
        f"  pajak {TAX_XMLID} -> {tax['name']} ({', '.join(tax['source_xmlids']) or 'buatan'}), "
        f"PPN untuk Rp1.000.000 = Rp{tax['ppn_on_million']:,.0f}".replace(",", ".")
    )
    return result


def main() -> int:
    ds = get_dataset()
    try:
        init_company(ds)
    except OdooShellError as exc:
        print(f"galat: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
