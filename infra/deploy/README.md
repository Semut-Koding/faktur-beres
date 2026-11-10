# Deploy ke VPS

Demo publik Faktur Beres berjalan di satu VPS (Ubuntu Server 26.04, 2 vCPU, RAM 2 GB) yang hanya dipakai proyek ini. Caddy menerima HTTPS dan meneruskannya ke layanan yang semuanya mendengarkan di `127.0.0.1`.

| Host | Diteruskan ke | Akses |
|---|---|---|
| `fakturberes.farhanginting.my.id` | API dan dashboard, `127.0.0.1:28000` | Baca publik; aksi butuh kode akses |
| `odoo.fakturberes.farhanginting.my.id` | Odoo, `127.0.0.1:28069` | Basic auth, lalu login Odoo |
| `mail.fakturberes.farhanginting.my.id` | Mailpit, `127.0.0.1:28025` | Basic auth |

Skrip di folder ini hanya untuk VPS. Aturan CLAUDE.md tentang `sudo`, systemd, firewall, dan paket sistem tetap berlaku penuh di laptop pengembang.

## Isi folder

| Berkas | Dijalankan di | Fungsi |
|---|---|---|
| `sinkron.sh` | laptop | Membangun dashboard, lalu rsync repo ke `~/faktur-beres` di VPS |
| `vps-siapkan.sh` | VPS, `sudo` | Swap 4 GB, zona waktu, paket (Podman, podman-compose, Caddy, ufw, rsync, pipx), firewall 22/80/443, linger |
| `vps-pasang.sh` | VPS | `uv`, `.env`, penyetelan memori, kontainer, init dan seed Odoo, snapshot, migrasi |
| `env-buat.sh` | VPS | Membuat `.env` dengan rahasia acak (`openssl rand`), `chmod 600`; `GEMINI_API_KEY` dibiarkan kosong |
| `vps-nyalakan.sh` | VPS | Indeks kebijakan, lalu layanan user systemd untuk kontainer dan API |
| `caddy-pasang.sh` | VPS, `sudo` | Memasang `Caddyfile` dengan hash sandi basic auth, lalu memuat ulang Caddy |
| `Caddyfile` | – | Templat tiga host dengan TLS otomatis; `basicauth` karena Ubuntu 26.04 membawa Caddy 2.6 (ejaan `basic_auth` baru ada sejak 2.8) |
| `compose.override.yaml` | – | PostgreSQL `shared_buffers` 64 MB; Odoo `--proxy-mode` dan batas memori |
| `systemd/` | – | Unit user `fakturberes-layanan` (kontainer) dan `fakturberes-api` (uvicorn, pemantau inbox) |

Semua skrip aman dijalankan ulang.

## Yang tidak pernah dikirim ke VPS

`sinkron.sh` mengecualikan `.env`, `.git`, `.venv`, `node_modules`, `var/`, folder pribadi, arsip `.zip`, dan seluruh isi `eval/` kecuali `eval/ringkasan-terakhir.json`. Label evaluasi (`eval/skenario/`, `eval/ketahanan/skenario/`, `eval/injeksi/`, `eval/DAFTAR.md`) dan berkas uji tidak boleh ada di mesin yang menjalankan agent. Dari `tests/` hanya sidik addon yang ikut, karena `odoo-init.sh` membacanya.

## Urutan deploy pertama

Prasyarat: login SSH dengan kunci, record DNS ketiga host mengarah ke VPS, dan port 80 serta 443 terbuka di firewall penyedia.

```bash
VPS=ubuntu@43.173.40.20

bash infra/deploy/sinkron.sh "$VPS"                                   # dari laptop
ssh "$VPS" 'sudo bash ~/faktur-beres/infra/deploy/vps-siapkan.sh'
ssh "$VPS" 'bash ~/faktur-beres/infra/deploy/vps-pasang.sh'
ssh -t "$VPS" 'nano ~/faktur-beres/.env'                              # pemilik mengisi GEMINI_API_KEY
ssh "$VPS" 'bash ~/faktur-beres/infra/deploy/vps-nyalakan.sh'
ssh "$VPS" 'sudo bash ~/faktur-beres/infra/deploy/caddy-pasang.sh'
```

Bila `sudo` meminta sandi, jalankan langkah itu dengan `ssh -t`.

Rahasia hanya ada di `~/faktur-beres/.env` di VPS dan tidak pernah dicetak skrip. Pemilik membacanya sendiri di VPS:

- `DEMO_KODE_AP` dan `DEMO_KODE_APPROVER`: kode akses peran di dashboard;
- `BASIC_AUTH_USER` dan `BASIC_AUTH_SANDI`: basic auth Odoo dan Mailpit;
- `ODOO_ADMIN_PASSWORD`: login `admin` di Odoo.

## Uji setelah deploy

```bash
curl -I https://fakturberes.farhanginting.my.id
curl -s https://fakturberes.farhanginting.my.id/api/metrik
curl -s -o /dev/null -w '%{http_code}\n' https://odoo.fakturberes.farhanginting.my.id   # 401 tanpa basic auth
```

Dari luar hanya port 22, 80, dan 443 yang terbuka.

## Memperbarui

```bash
bash infra/deploy/sinkron.sh "$VPS"
ssh "$VPS" 'cd ~/faktur-beres && ~/.local/bin/uv sync --quiet && ~/.local/bin/uv run python -m faktur_beres.db migrasi'
ssh "$VPS" 'systemctl --user restart fakturberes-api'
```

Bila `Caddyfile` berubah, jalankan lagi `caddy-pasang.sh`. Bila unit systemd berubah, jalankan lagi `vps-nyalakan.sh`.

## Mengembalikan demo ke kondisi awal

Dijalankan di VPS, di `~/faktur-beres`. API dihentikan dulu supaya tidak ada faktur yang sedang diproses.

```bash
systemctl --user stop fakturberes-api
bash infra/scripts/odoo-reset.sh                                       # Odoo kembali ke snapshot seed
~/.local/bin/uv run python -m faktur_beres.db reset                    # kosongkan tabel aplikasi (indeks kebijakan tetap)
curl -fsS -X DELETE http://127.0.0.1:28025/api/v1/messages             # kosongkan Mailpit
rm -rf var/berkas                                                      # berkas faktur yang diunggah
systemctl --user start fakturberes-api
```

Node tidak dipasang di VPS, jadi perintah di balik skrip `npm run` dijalankan langsung seperti di atas.

## Memeriksa

```bash
systemctl --user status fakturberes-api fakturberes-layanan
journalctl --user -u fakturberes-api -n 100
bash infra/scripts/services-status.sh
free -m
```

## Batasan

- RAM 2 GB dengan swap 4 GB: Odoo berjalan tanpa worker (`workers = 0`), cukup untuk demo, bukan untuk beban produksi.
- Batas laju unggah (10 per jam per IP) dan kunci per faktur disimpan di memori satu proses API, jadi API dijalankan sebagai satu proses.
- Mailpit adalah kotak surat sandbox: tidak ada email yang keluar dari VPS.
- Key API Odoo dari `odoo-init.sh` berlaku 90 hari.
