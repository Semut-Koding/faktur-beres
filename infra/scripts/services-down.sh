#!/usr/bin/env bash
# npm run services:down — hentikan dan hapus kontainer fakturberes. Volume (data) tetap ada.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Sengaja tanpa -v: volume bernama berisi data Odoo, aplikasi, dan Mailpit.
compose down
echo "Layanan $PROJECT dimatikan. Volume tetap ada; nyalakan lagi dengan npm run services:up."
