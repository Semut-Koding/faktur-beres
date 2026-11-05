"""Company data, base URL, and purchase tax (runs inside `odoo shell`; `env` is provided).

Input: JSON in the FB_PARAMS environment variable. Output: one line ``FB_RESULT <json>`` on
stdout. Idempotent: only values that differ are written; one commit at the end.
"""

import json
import os

P = json.loads(os.environ["FB_PARAMS"])
steps = []


def report(item, status):
    steps.append([item, status])


def find_state(country, name):
    State = env["res.country.state"]
    state = State.search([("country_id", "=", country.id), ("name", "=ilike", name)], limit=1)
    if not state:
        # Odoo names some provinces without their prefix (e.g. "DKI Jakarta" -> "Jakarta").
        candidates = State.search([("country_id", "=", country.id)])
        state = candidates.filtered(lambda s: s.name.lower() in name.lower())[:1]
    return state


# -- company ----------------------------------------------------------------------------------
cdata = P["company"]
company = env.ref("base.main_company")
country = env["res.country"].search([("code", "=", cdata["country"])], limit=1)
currency = (
    env["res.currency"]
    .with_context(active_test=False)
    .search([("name", "=", cdata["currency"])], limit=1)
)
state = find_state(country, cdata["state"])
if not (country and currency and state):
    raise SystemExit(f"negara/mata uang/provinsi tidak ditemukan: {cdata}")

wanted = {
    "name": cdata["name"],
    "vat": cdata["npwp"],
    "street": cdata["street"],
    "city": cdata["city"],
    "zip": cdata["zip"],
    "state_id": state.id,
    "country_id": country.id,
    "email": cdata["email"],
    "website": cdata["website"],
}
diff = {}
for field, value in wanted.items():
    current = company[field]
    current = current.id if hasattr(current, "_name") else (current or "")
    if current != value:
        diff[field] = value
if diff:
    company.write(diff)
    report("perusahaan", "diubah: " + ", ".join(sorted(diff)))
else:
    report("perusahaan", "sudah ada")

if company.currency_id != currency:
    company.currency_id = currency
    report("mata uang", f"diubah ke {currency.name}")
else:
    report("mata uang", f"sudah {currency.name}")

for user in env["res.users"].search([("share", "=", False), ("id", "!=", 1)]):
    if user.tz != P["tz"]:
        user.tz = P["tz"]
        report(f"zona waktu {user.login}", f"diubah ke {P['tz']}")

# -- accounting -------------------------------------------------------------------------------
if company.chart_template == P["chart_template"]:
    report("chart of accounts", f"sudah terpasang ({company.chart_template})")
else:
    env["account.chart.template"].try_loading(P["chart_template"], company, install_demo=False)
    report("chart of accounts", f"dipasang ({P['chart_template']})")

journal = env["account.journal"].search(
    [("type", "=", "purchase"), ("company_id", "=", company.id)], limit=1
)
if not journal:
    raise SystemExit("jurnal tagihan vendor (type purchase) tidak ada")
report("jurnal tagihan vendor", f"ada: {journal.code} ({journal.name})")

# -- base URL ---------------------------------------------------------------------------------
ICP = env["ir.config_parameter"].sudo()
for key, value in (("web.base.url", P["base_url"]), ("web.base.url.freeze", "True")):
    if ICP.get_param(key) != value:
        ICP.set_param(key, value)
        report(key, f"diubah ke {value}")
    else:
        report(key, "sudah ada")

# -- purchase tax -----------------------------------------------------------------------------
tdata = P["tax"]


def ppn_on_million(tax):
    res = tax.compute_all(1_000_000.0, currency=company.currency_id)
    return round(res["total_included"] - res["total_excluded"], 2)


chosen = env.ref(f"account.{company.id}_{tdata['localization_template']}", raise_if_not_found=False)
if chosen and (chosen.price_include or ppn_on_million(chosen) != tdata["ppn_on_million"]):
    chosen = None
if not chosen:
    for tax in env["account.tax"].search(
        [("type_tax_use", "=", "purchase"), ("company_id", "=", company.id)]
    ):
        if not tax.price_include and ppn_on_million(tax) == tdata["ppn_on_million"]:
            chosen = tax
            break
if not chosen:
    raise SystemExit(
        "tidak ada pajak pembelian lokalisasi dengan PPN 110.000 per 1.000.000; "
        "pajak sendiri perlu dibuat (lihat RENCANA-TEKNIS §4.4)"
    )

module, name = tdata["xmlid"].split(".")
IMD = env["ir.model.data"].sudo()
imd = IMD.search([("module", "=", module), ("name", "=", name)], limit=1)
if not imd:
    IMD.create(
        {
            "module": module,
            "name": name,
            "model": "account.tax",
            "res_id": chosen.id,
            "noupdate": True,
        }
    )
    report("pajak pembelian", f"dipilih: {chosen.name} (id {chosen.id})")
elif imd.res_id != chosen.id or imd.model != "account.tax":
    imd.write({"model": "account.tax", "res_id": chosen.id})
    report("pajak pembelian", f"diubah ke {chosen.name} (id {chosen.id})")
else:
    report("pajak pembelian", f"sudah ada: {chosen.name} (id {chosen.id})")

if company.account_purchase_tax_id != chosen:
    company.account_purchase_tax_id = chosen
    report("pajak pembelian bawaan perusahaan", "diubah")
else:
    report("pajak pembelian bawaan perusahaan", "sudah sesuai")

source_xmlids = sorted(
    f"{d.module}.{d.name}"
    for d in IMD.search([("model", "=", "account.tax"), ("res_id", "=", chosen.id)])
    if d.module != module
)

env.cr.commit()
print(
    "FB_RESULT "
    + json.dumps(
        {
            "steps": steps,
            "tax": {
                "id": chosen.id,
                "name": chosen.name,
                "xmlid": tdata["xmlid"],
                "source_xmlids": source_xmlids,
                "amount": chosen.amount,
                "ppn_on_million": ppn_on_million(chosen),
            },
            "journal": {"id": journal.id, "code": journal.code},
        }
    )
)
