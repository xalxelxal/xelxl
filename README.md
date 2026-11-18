# MYnyak Engsel Sunset — Versi Web

Versi web dari **MYnyak Engsel Sunset** — CLI (aplikasi terminal) tidak resmi
untuk MyTelkomsel, dari repo
[purplemashu/me-cli-sunset](https://github.com/purplemashu/me-cli-sunset)
(dokumen asli repo ada di `README-ORIGINAL.md`).

Aplikasi ini **memakai ulang API layer repo asli** (`app/client/`: engsel,
ciam, encrypt, famplan, circle, registration, purchase, store, plus
`app/service/crypto_helper.py`) dan mengganti menu teks interaktif
(`app/menus/`, `main.py`) dengan antarmuka web Flask. Jadi semua fitur CLI
tersedia lewat browser, tanpa Termux.

> ⚠️ **Bukan aplikasi resmi Telkomsel / MyXL.** Ini client pihak ketiga yang
> berbicara langsung ke API MyXL dengan token akunmu. Baca bagian Risiko di
> bawah sebelum memakai.

## Fitur (memetakan menu CLI)

| Menu CLI | Halaman web |
|---|---|
| Profil (nomor, pulsa, masa aktif, poin/tier) | Header profil di semua halaman |
| 1. Login / ganti akun (OTP SMS) | **Akun** — tambah akun, kirim OTP, ganti/hapus akun |
| 2. Lihat Paket Saya | **Paket Saya** (+ Berhenti berlangganan) |
| 3. Beli Paket HOT | **HOT 🔥** (dari `hot_data/hot.json`) |
| 4. Beli Paket HOT-2 | **HOT-2 🔥** (dari `hot_data/hot2.json`) |
| 5. Beli by Option Code | **Beli Kode** |
| 6. Beli by Family Code | **Beli Family** |
| 7. Beli Semua Paket di Family (loop) | **Beli Family → Beli Semua (Loop)**, jalan di latar belakang dengan halaman progres (polling) |
| 8. Riwayat Transaksi | **Riwayat** (+ transaksi pending) |
| 9. Family Plan / Akrab | **Akrab** — anggota, ganti anggota, hapus anggota, atur kuota |
| 10. Circle | **Circle** — grup, anggota, undang/hapus/terima, validasi, bonus, spending tracker |
| 11. Store Segments | **Store → Segments** (menu Store ada di navigasi: Segments, Family List, Packages, Redeemables) |
| 12. Store Family List | **Store → Family List** |
| 13. Store Packages | **Store → Packages** |
| 14. Redemables | **Store → Redeemables** |
| R. Register (Dukcapil NIK+KK) | **Registrasi** (+ validasi PUK & validasi nomor) |
| N. Notifikasi | **Notifikasi** |
| V. Validate MSISDN | **Registrasi → Validasi Nomor** dan halaman **Validasi** |
| 00. Bookmark Paket | **Bookmark** (tambah dari halaman Detail Paket) |

Detail paket (menu di CLI) tersedia di halaman **Detail Paket** dengan semua
cara bayar yang sama seperti CLI:

1. Pulsa, 2. E-Wallet (DANA / ShopeePay / GoPay / OVO), 3. QRIS (kode QR
   ditampilkan sebagai gambar di halaman, beserta transaction ID),
   4. Pulsa + Decoy, 5. Pulsa + Decoy V2, 6. QRIS + Decoy, 7. QRIS + Decoy V2,
   8. Beli N kali (pulsa, di latar belakang), plus Ambil sebagai bonus,
   Kirim bonus, dan Beli dengan Poin.

## Cara menjalankan

```bash
cd me-cli-sunset-web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
# Buka http://127.0.0.1:5000
```

Untuk production, jangan pakai server development Flask — jalankan lewat
WSGI server (mis. `gunicorn -w 2 -b 0.0.0.0:5000 app:app`) di belakang
reverse proxy dengan HTTPS.

## Password web app

Halaman web ini dilindungi password sendiri (bukan password MyXL):

- Atur lewat environment variable `WEB_PASSWORD` sebelum menjalankan server.
- Kalau belum diatur, saat pertama kali jalan aplikasi membuat password acak,
  mencetaknya **sekali** di terminal, dan menyimpannya di
  `data/.web_password` (chmod 600) supaya tidak terkunci setelah restart.
- Untuk mengganti: set `WEB_PASSWORD` yang baru, atau hapus
  `data/.web_password` lalu restart (password acak baru dibuat).

## Mengisi .env (wajib sebelum fitur Telkomsel bisa jalan)

Repo asli hanya menyediakan `.env.template` yang **kosong** (lihat
`.env.example`). Nilai aslinya tidak ada di repo — dibagikan lewat channel
Telegram pembuat tool ini. Aplikasi ini **tidak** mengambil nilai tersebut
dari mana pun; kamu yang mengisinya.

Cara isi — lewat halaman **Pengaturan** di web (paling mudah):

1. Buka **Pengaturan** — halaman ini menampilkan kunci mana yang sudah
   terisi dan mana yang masih kosong.
2. Isi nilai-nilainya, klik **Simpan .env & Muat Ulang API**.
3. Aplikasi menulis file `.env` di folder aplikasi dan memuat ulang modul
   API (modul-modul itu membaca environment saat di-import, jadi tidak perlu
   restart).

Atau isi manual: salin `.env.example` menjadi `.env` dan isi nilainya,
lalu restart server.

Kunci yang wajib: `BASE_API_URL`, `BASE_CIAM_URL`, `BASIC_AUTH`,
`AX_FP_KEY`, `UA`, `API_KEY`, `ENCRYPTED_FIELD_KEY`, `XDATA_KEY`,
`AX_API_SIG_KEY`, `X_API_BASE_SECRET`. Opsional: `CIRCLE_MSISDN_KEY`,
`AES_KEY_ASCII`, `AX_FP`.

**Tanpa nilai-nilai ini, semua fitur Telkomsel gagal** — tapi aplikasi tetap
bisa dibuka: halaman-halaman menampilkan pesan jelas
“.env belum lengkap” dan bukan crash. Cek cepat lewat
`http://127.0.0.1:5000/kesehatan` (JSON: apakah .env lengkap, apakah modul
API bisa di-import, berapa akun tersimpan — tanpa menampilkan nilai rahasia).

Catatan soal form Pengaturan: kolom nilai selalu dikosongkan saat halaman
dimuat (nilai tidak pernah dikirim kembali ke browser). Jadi **isi ulang
semua kunci yang mau disimpan** setiap kali menyimpan — kunci yang dibiarkan
kosong akan dihapus dari `.env`.

## Login akun MyXL

Di menu **Akun**: masukkan nomor (awalan `628`), klik **Kirim OTP**, lalu
masukkan kode OTP 6 digit dari SMS. Sama seperti CLI, ada 5x percobaan.

Token refresh disimpan di `data/refresh-tokens.json` — formatnya **sama**
dengan CLI Termux, jadi file ini bisa dipakai bergantian dengan Termux
(dan sebaliknya). Token akses di-refresh otomatis tiap 5 menit, seperti CLI.

## Cara kerja teknis (singkat)

- `app.py` — aplikasi Flask: semua route, proteksi login web (session),
  dan pemanggilan API yang dibungkus agar gagal dengan pesan yang jelas.
- `telkomsel.py` — jembatan: memuat `.env`, import lazy `app/client/*`
  (modul-modul itu gagal import kalau `BASE_API_URL`/`BASE_CIAM_URL`
  kosong — karena itu import dilakukan setelah .env dimuat, dan modul
  dibuang dari `sys.modules` setelah .env disimpan ulang), menyimpan token,
  profil, bookmark (`data/bookmarks.json`), decoy (`decoy_data/*.json`),
  pembelian (pulsa/QRIS/e-wallet/redeem/decoy/N kali/loop family), dan
  background job (thread + polling `/api/job/<id>`).
- `app/service/auth.py` dan `app/menus/*` dari repo asli **tidak dipakai**:
  singleton Auth CLI memanggil `input()` dan langsung menelepon API saat
  import — logika yang sama ditulis ulang di `telkomsel.py` dalam bentuk
  yang aman untuk web (tanpa `input()` di jalur apa pun). Yang dipertahankan
  dari `app/menus/` hanya `util.py` (fungsi `format_quota_byte` /
  `display_html`), karena `app/client/famplan.py` mengimpornya.
- Pembayaran memanggil fungsi settlement repo asli dengan
  `ask_overwrite=False` + `overwrite_amount` eksplisit — karena fungsi
  tersebut memanggil `input()` kalau `ask_overwrite=True`.
- File sensitif (`.env`, `data/`, token, `ax.fp`) masuk `.gitignore` dan
  tidak boleh di-commit. Nilai sensitif tidak pernah ditulis ke log atau
  ditampilkan di UI (hanya status “terisi / kosong”).

## Deploy

Butuh server Python yang jalan terus (Flask + `requests`). **Cloudflare
Workers TIDAK cocok** (bukan Python, dan aplikasi ini stateful: token,
job, session).

Pilihan yang cocok:

- **VPS** (paling fleksibel): clone repo ini, buat venv, jalankan dengan
  gunicorn + systemd, pasang Nginx + HTTPS (mis. Caddy/Let's Encrypt).
- **Railway / PaaS Python lain**: deploy dari repo ini; set `WEB_PASSWORD`
  dan isi `.env` lewat halaman Pengaturan setelah deploy (atau lewat
  environment variable service-nya — nilai env dari hosting akan dibaca
  juga, tapi nilai yang disimpan lewat Pengaturan ditulis ke file `.env`).
- Port default `5000`, bisa diubah lewat env `PORT`. Aplikasi hanya
  mendengarkan `127.0.0.1` secara default — ubah `host` di `app.py` kalau
  mau diakses dari luar (wajib pakai HTTPS + password yang kuat).

## Risiko — baca dulu

- **Client tidak resmi.** Tool ini meniru aplikasi MyXL dan berbicara ke
  API internal Telkomsel. Akun bisa diblokir / dibatasi oleh Telkomsel,
  dan paket yang dibeli lewat jalur ini (terutama paket “decoy” dan paket
  yang sudah tidak tampil di aplikasi resmi) bisa dicabut atau tidak
  diakui. Repo aslinya sendiri sudah **di-archive (sunset)** — API MyXL
  bisa berubah sewaktu-waktu dan tool ini berhenti jalan.
- **Kredensial & identitas.** Aplikasi menyimpan refresh token Telkomsel,
  dan fitur Registrasi meminta NIK + nomor KK. Jangan jalankan aplikasi
  ini di server yang bisa diakses orang lain tanpa password web yang kuat
  dan HTTPS. Jangan pernah membagikan isi `.env`, `data/refresh-tokens.json`,
  atau token apa pun.
- **Uang.** Semua tombol beli langsung menagih (pulsa / e-wallet / QRIS).
  Selalu periksa harga dan nomor aktif di header sebelum membeli; pembelian
  loop family membeli **semua** paket di family itu satu per satu.
- **Tidak ada garansi.** Pakai dengan risiko sendiri. Kalau ragu, pakai
  aplikasi MyXL resmi.
