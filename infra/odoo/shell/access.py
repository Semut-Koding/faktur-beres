"""Admin password, agent bot user, and JSON-2 API keys (runs inside `odoo shell`).

Input: JSON in FB_PARAMS; secrets in FB_ADMIN_PASSWORD, FB_ADMIN_KEY, FB_AGENT_KEY (the keys
currently in .env, possibly empty). Output: one line ``FB_RESULT <json>``. A new API key is only
generated when the current one is missing, invalid, or expired, and only new keys are returned.
Idempotent; one commit at the end.

API keys use ``res.users.apikeys._generate(scope, name, expiration_date)`` (Odoo 19,
odoo/addons/base/models/res_users.py). The admin key is generated in sudo. The agent key is
generated as the agent user itself, *without* sudo, so ``_check_expiration_date`` applies the
largest ``api_key_duration`` of the agent's groups (90 days on faktur_beres_agent.group_agent).
A valid agent key with an older name (made in sudo before M2) is rotated once.
"""

import json
import os
from datetime import timedelta

from odoo import Command, fields

P = json.loads(os.environ["FB_PARAMS"])
steps = []
new_keys = {}
expiries = {}


def report(item, status):
    steps.append([item, status])


# -- admin password ---------------------------------------------------------------------------
admin = env.ref("base.user_admin")
password = os.environ.get("FB_ADMIN_PASSWORD", "")
if not password:
    raise SystemExit("ODOO_ADMIN_PASSWORD kosong; jalankan npm run setup")
env.cr.execute("SELECT COALESCE(password, '') FROM res_users WHERE id = %s", [admin.id])
stored = env.cr.fetchone()[0]
if stored and admin._crypt_context().verify(password, stored):
    report("sandi admin", "sudah sesuai .env")
else:
    admin.password = password
    report("sandi admin", "diatur dari ODOO_ADMIN_PASSWORD")

# -- agent bot user ---------------------------------------------------------------------------
a = P["agent"]
module, name = a["xmlid"].split(".")
IMD = env["ir.model.data"].sudo()
Users = env["res.users"].with_context(active_test=False, no_reset_password=True)
imd = IMD.search([("module", "=", module), ("name", "=", name)], limit=1)
agent = Users.browse(imd.res_id).exists() if imd else Users.browse()
if not agent:
    agent = Users.search([("login", "=", a["login"])], limit=1)
groups = env["res.groups"].browse([env.ref(x).id for x in a["groups"]])

wanted = {
    "name": a["name"],
    "login": a["login"],
    "email": a["login"],
    "lang": a["lang"],
    "tz": a["tz"],
    "active": True,
}
# The bot never gets e-mail notifications. In Odoo 19 `notification_type = "inbox"` is stored as
# membership of mail.group_mail_notification_type_inbox, so that group is part of the wanted set.
inbox_group = env.ref("mail.group_mail_notification_type_inbox", raise_if_not_found=False)
if "notification_type" in Users._fields and inbox_group:
    wanted["notification_type"] = "inbox"
    groups |= inbox_group

if not agent:
    agent = Users.create({**wanted, "group_ids": [Command.set(groups.ids)]})
    report(f"user {a['login']}", "dibuat (tanpa sandi)")
else:
    diff = {k: v for k, v in wanted.items() if agent[k] != v}
    if diff:
        agent.write(diff)
        report(f"user {a['login']}", "diubah: " + ", ".join(sorted(diff)))
    else:
        report(f"user {a['login']}", "sudah ada")
    if set(agent.group_ids.ids) != set(groups.ids):
        agent.group_ids = [Command.set(groups.ids)]
        report("grup user agent", "diatur ulang")
if "notification_type" in wanted and agent.notification_type != wanted["notification_type"]:
    raise SystemExit("notification_type user agent tidak tersimpan")
if set(agent.group_ids.ids) != set(groups.ids):
    raise SystemExit("grup user agent tidak persis sama dengan yang diminta")

env.cr.execute("SELECT COALESCE(password, '') FROM res_users WHERE id = %s", [agent.id])
if env.cr.fetchone()[0]:
    raise SystemExit("user agent punya sandi; user bot harus tanpa sandi")
if not imd:
    IMD.create(
        {"module": module, "name": name, "model": "res.users", "res_id": agent.id, "noupdate": True}
    )
elif imd.res_id != agent.id:
    imd.res_id = agent.id

explicit = sorted(g.get_external_id().get(g.id) or g.name for g in agent.group_ids)
report(
    "grup user agent", ", ".join(explicit) + f" ({len(agent.all_group_ids)} grup termasuk tersirat)"
)

# -- API keys ---------------------------------------------------------------------------------
Keys = env["res.users.apikeys"].sudo()


key_modes = {}


def ensure_key(label, user, current, key_name, *, old_names=(), without_sudo=False):
    uid = Keys._check_credentials(scope=P["scope"], key=current) if current else None
    reason = "kosong di .env" if not current else "tidak valid/kedaluwarsa"
    if uid == user.id:
        env.cr.execute(
            "SELECT name, expiration_date FROM res_users_apikeys WHERE user_id = %s AND index = %s",
            [user.id, current[:8]],
        )
        row = env.cr.fetchone()
        if row and row[0] == key_name:
            expiries[label] = str(row[1])[:10] if row[1] else None
            key_modes[label] = "tanpa sudo" if without_sudo else "sudo"
            report(f"API key {label}", f"sudah ada, berlaku sampai {expiries[label]}")
            return
        reason = "diputar: key lama dibuat dengan sudo"
    # Remove keys this tool created earlier for the user; keys made by hand are left alone.
    Keys.search([("user_id", "=", user.id), ("name", "in", [key_name, *old_names])])._remove()
    expiration = fields.Datetime.now() + timedelta(days=P["key_days"])
    if without_sudo:
        # As the user itself: Odoo enforces the api_key_duration of the user's groups.
        key = env["res.users.apikeys"].with_user(user)._generate(P["scope"], key_name, expiration)
        key_modes[label] = "tanpa sudo"
    else:
        key = Keys.with_user(user).sudo()._generate(P["scope"], key_name, expiration)
        key_modes[label] = "sudo"
    new_keys[label] = key
    expiries[label] = str(expiration)[:10]
    report(
        f"API key {label}",
        f"dibuat {key_modes[label]} ({reason}), berlaku sampai {expiries[label]}",
    )


ensure_key("admin", admin, os.environ.get("FB_ADMIN_KEY", ""), P["admin_key_name"])
ensure_key(
    "agent",
    agent,
    os.environ.get("FB_AGENT_KEY", ""),
    a["key_name"],
    old_names=a["old_key_names"],
    without_sudo=True,
)

env.cr.commit()
print(
    "FB_RESULT "
    + json.dumps(
        {
            "steps": steps,
            "keys": new_keys,
            "expiries": expiries,
            "key_modes": key_modes,
            "agent": {
                "id": agent.id,
                "groups": explicit,
                "all_groups": sorted(
                    g.get_external_id().get(g.id) or g.name for g in agent.all_group_ids
                ),
            },
        }
    )
)
