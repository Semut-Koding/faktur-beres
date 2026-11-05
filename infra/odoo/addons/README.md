# infra/odoo/addons

Folder ini sengaja kosong. Folder di-bind ke `/mnt/extra-addons` di kontainer Odoo (read-only)
supaya Podman tidak membuat volume anonim untuk `VOLUME` image tersebut. Faktur Beres tidak
memakai modul Odoo kustom ([ADR-0004](../../../docs/adr/0004-odoo-19-json2-api.md)), dan folder
ini tidak ada di `addons_path`.
