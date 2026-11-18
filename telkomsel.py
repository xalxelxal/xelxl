"""Jembatan antara web app (Flask) dan API layer me-cli-sunset.

Isi repo asli yang dipakai ulang (tanpa diubah):
  app/client/engsel.py, ciam.py, encrypt.py, famplan.py, circle.py,
  registration.py, purchase/*, store/*, app/service/crypto_helper.py,
  app/type_dict.py, app/menus/util.py (hanya format_quota_byte/display_html).

Yang TIDAK dipakai dari repo asli:
  app/service/auth.py (singleton CLI yang memanggil input() dan langsung
  menelepon API saat import), app/menus/* (menu teks interaktif), main.py.
  Logika yang sama ditulis ulang di sini dalam bentuk yang aman untuk web:
  token disimpan di data/refresh-tokens.json (format yang sama dengan CLI,
  jadi token bisa dipakai bergantian dengan Termux), dan tidak ada satu pun
  pemanggilan input() di jalur web.

PENTING: modul-modul app/client/* membaca environment variable saat IMPORT
(BASE_API_URL dan BASE_CIAM_URL wajib ada, kalau tidak import langsung
gagal dengan ValueError). Karena itu semua import client dilakukan secara
LAZY di get_client(), setelah .env dimuat, dan setelah user menyimpan .env
baru lewat halaman Pengaturan, modul-modul itu dibuang dari sys.modules
supaya import berikutnya membaca nilai yang baru.
"""
import base64
import io
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
ENV_FILE = BASE / ".env"
TOKENS_FILE = DATA / "refresh-tokens.json"
ACTIVE_FILE = DATA / "active.number"
BOOKMARKS_FILE = DATA / "bookmarks.json"

DATA.mkdir(exist_ok=True)

# .env dimuat SEBELUM import app.client.* apa pun.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENV_FILE, override=True)

# Nilai api_key yang dipakai CLI (app/util.py ensure_api_key() -> "Noir1").
API_KEY = "Noir1"

ENV_KEYS = [
    ("BASE_API_URL", "URL dasar API MyXL", True, True),
    ("BASE_CIAM_URL", "URL dasar CIAM (login/OTP)", True, True),
    ("BASIC_AUTH", "Basic auth CIAM", True, True),
    ("AX_FP_KEY", "Kunci fingerprint perangkat (AX_FP_KEY)", True, True),
    ("UA", "User-Agent aplikasi MyXL", False, True),
    ("API_KEY", "API key header (x-api-key)", True, True),
    ("ENCRYPTED_FIELD_KEY", "Kunci field terenkripsi", True, True),
    ("XDATA_KEY", "Kunci enkripsi x-data", True, True),
    ("AX_API_SIG_KEY", "Kunci signature API", True, True),
    ("X_API_BASE_SECRET", "Secret dasar X-API", True, True),
    ("CIRCLE_MSISDN_KEY", "Kunci enkripsi nomor Circle", True, False),
    ("AES_KEY_ASCII", "Kunci AES (AES_KEY_ASCII)", True, False),
    ("AX_FP", "Fingerprint perangkat (AX_FP, opsional)", True, False),
]
ENV_KEY_NAMES = [k[0] for k in ENV_KEYS]
REQUIRED_ENV = [k[0] for k in ENV_KEYS if k[3]]


class TelkomselError(Exception):
    """Error yang aman ditampilkan ke user (tanpa token/kunci)."""


def _redact(text):
    """Jangan pernah membocorkan nilai sensitif ke log/UI."""
    if not isinstance(text, str):
        text = str(text)
    for key in ENV_KEY_NAMES:
        val = os.environ.get(key)
        if val and len(val) > 4 and val in text:
            text = text.replace(val, f"[{key} disembunyikan]")
    return text


def parse_env_file():
    """Baca .env sebagai dict (tanpa mengubah os.environ)."""
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                values[key] = val
    return values


def env_status():
    """Status tiap kunci .env: terisi / kosong. Nilai TIDAK pernah dikembalikan."""
    values = parse_env_file()
    out = []
    for key, label, sensitive, required in ENV_KEYS:
        filled = bool(values.get(key) or os.environ.get(key))
        out.append({"key": key, "label": label, "sensitive": sensitive,
                    "required": required, "filled": filled})
    missing_required = [s["key"] for s in out if s["required"] and not s["filled"]]
    return {"entries": out, "missing_required": missing_required,
            "ready": not missing_required, "env_file_exists": ENV_FILE.exists()}


def save_env(values):
    """Simpan nilai .env dari form Pengaturan, lalu muat ulang modul client.

    Hanya kunci yang dikenal yang disimpan. Nilai kosong = hapus kunci itu.
    Setelah menyimpan, modul app.client.* dibuang dari sys.modules agar
    import berikutnya membaca nilai baru (modul-modul itu membaca env
    saat import).
    """
    lines = []
    for key in ENV_KEY_NAMES:
        val = (values.get(key) or "").strip()
        if val:
            safe = val.replace('"', '\\"')
            lines.append(f'{key}="{safe}"')
            os.environ[key] = val
        else:
            os.environ.pop(key, None)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    purge_client_modules()
    load_dotenv(ENV_FILE, override=True)
    return env_status()


def purge_client_modules():
    global _CLIENT
    _CLIENT = None
    for name in list(sys.modules):
        if name == "app.client" or name.startswith("app.client.") \
                or name.startswith("app.service.crypto_helper") \
                or name == "app.menus.util":
            del sys.modules[name]


_CLIENT = None
_CLIENT_ERROR = None


def get_client():
    """Import (lazy) semua fungsi client. Gagal dengan pesan jelas jika
    .env belum lengkap — bukan crash."""
    global _CLIENT, _CLIENT_ERROR
    if _CLIENT is not None:
        return _CLIENT
    status = env_status()
    if status["missing_required"]:
        raise TelkomselError(
            "Pengaturan .env belum lengkap. Kunci yang masih kosong: "
            + ", ".join(status["missing_required"])
            + ". Isi dulu di menu Pengaturan.")
    try:
        from app.client import ciam, engsel, famplan, circle, registration
        from app.client.purchase import balance as purchase_balance
        from app.client.purchase import qris as purchase_qris
        from app.client.purchase import ewallet as purchase_ewallet
        from app.client.purchase import redeem as purchase_redeem
        from app.client.purchase import common as purchase_common
        from app.client.store import segments as store_segments
        from app.client.store import search as store_search
        from app.client.store import redeemables as store_redeemables
        from app.menus.util import format_quota_byte, display_html
    except Exception as exc:  # noqa: BLE001 - tampilkan sebagai pesan user
        _CLIENT_ERROR = _redact(exc)
        raise TelkomselError(
            "Gagal memuat modul API MyXL: " + _redact(exc)
            + " — periksa kembali nilai .env di menu Pengaturan.") from exc
    _CLIENT = {
        "ciam": ciam, "engsel": engsel, "famplan": famplan, "circle": circle,
        "registration": registration,
        "purchase_balance": purchase_balance, "purchase_qris": purchase_qris,
        "purchase_ewallet": purchase_ewallet, "purchase_redeem": purchase_redeem,
        "purchase_common": purchase_common,
        "store_segments": store_segments, "store_search": store_search,
        "store_redeemables": store_redeemables,
        "format_quota_byte": format_quota_byte, "display_html": display_html,
    }
    return _CLIENT


def call(fn, *args, **kwargs):
    """Panggil fungsi client dan normalisasi hasilnya.

    Mengembalikan dict/list apa adanya; kalau hasilnya string/None atau
    request gagal, lempar TelkomselError dengan pesan yang aman.
    """
    try:
        result = fn(*args, **kwargs)
    except TelkomselError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TelkomselError("Permintaan ke server MyXL gagal: " + _redact(exc)) from exc
    if result is None:
        raise TelkomselError("Server MyXL tidak mengembalikan data. "
                             "Periksa .env, login, atau coba lagi nanti.")
    if isinstance(result, str):
        raise TelkomselError("Server MyXL mengembalikan jawaban yang tidak "
                             "bisa dibaca: " + _redact(result[:300]))
    return result


# ---------------------------------------------------------------- token store
def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return default


def _save_json(path, data):
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")


def load_accounts():
    """Daftar akun tersimpan: [{number, subscriber_id, subscription_type,
    refresh_token}] — tanpa token akses (hanya refresh token)."""
    accounts = _load_json(TOKENS_FILE, [])
    return accounts if isinstance(accounts, list) else []


def save_accounts(accounts):
    _save_json(TOKENS_FILE, accounts)


def active_number():
    if ACTIVE_FILE.exists():
        text = ACTIVE_FILE.read_text(encoding="utf-8").strip()
        if text.isdigit():
            return int(text)
    return None


def set_active_number(number):
    if number is None:
        if ACTIVE_FILE.exists():
            ACTIVE_FILE.unlink()
    else:
        ACTIVE_FILE.write_text(str(number), encoding="utf-8")


def get_active_account():
    """Akun aktif + token akses yang masih segar (refresh bila >5 menit).

    Mengembalikan dict {number, subscriber_id, subscription_type, tokens}
    atau None bila belum ada akun / refresh gagal.
    """
    accounts = load_accounts()
    if not accounts:
        return None
    number = active_number() or accounts[0]["number"]
    entry = next((a for a in accounts if a["number"] == number), accounts[0])
    client = get_client()
    tokens = _refresh(entry, client)
    if not tokens:
        return None
    return {"number": entry["number"],
            "subscriber_id": entry.get("subscriber_id", ""),
            "subscription_type": entry.get("subscription_type", ""),
            "tokens": tokens}


def _refresh(entry, client):
    """Refresh token akses; simpan refresh token baru seperti CLI."""
    try:
        tokens = client["ciam"].get_new_token(
            API_KEY, entry["refresh_token"], entry.get("subscriber_id", ""))
    except Exception:  # noqa: BLE001
        return None
    if not tokens or not isinstance(tokens, dict) or "id_token" not in tokens:
        return None
    entry["refresh_token"] = tokens.get("refresh_token", entry["refresh_token"])
    # Perbarui info profil bila memungkinkan (jangan gagalkan login karenanya)
    try:
        profile = client["engsel"].get_profile(
            API_KEY, tokens["access_token"], tokens["id_token"])
        if isinstance(profile, dict):
            prof = profile.get("profile", profile)
            entry["subscriber_id"] = prof.get("subscriber_id", entry.get("subscriber_id", ""))
            entry["subscription_type"] = prof.get("subscription_type", entry.get("subscription_type", ""))
    except Exception:  # noqa: BLE001
        pass
    save_accounts(load_accounts_with_update(entry))
    return tokens


def load_accounts_with_update(updated_entry):
    accounts = load_accounts()
    for i, acc in enumerate(accounts):
        if acc["number"] == updated_entry["number"]:
            accounts[i] = updated_entry
            break
    else:
        accounts.append(updated_entry)
    return accounts


def add_account(number, refresh_token):
    """Simpan refresh token baru (setelah OTP berhasil) dan jadikan aktif."""
    client = get_client()
    tokens = call(client["ciam"].get_new_token, API_KEY, refresh_token, "")
    if not isinstance(tokens, dict) or "id_token" not in tokens:
        raise TelkomselError("Token dari server tidak valid. Coba login ulang.")
    entry = {"number": int(number), "subscriber_id": "",
             "subscription_type": "", "refresh_token": tokens.get("refresh_token", refresh_token)}
    try:
        profile = client["engsel"].get_profile(
            API_KEY, tokens["access_token"], tokens["id_token"])
        if isinstance(profile, dict):
            prof = profile.get("profile", profile)
            entry["subscriber_id"] = prof.get("subscriber_id", "")
            entry["subscription_type"] = prof.get("subscription_type", "")
    except Exception:  # noqa: BLE001
        pass
    save_accounts(load_accounts_with_update(entry))
    set_active_number(entry["number"])
    return entry


def remove_account(number):
    accounts = [a for a in load_accounts() if a["number"] != int(number)]
    save_accounts(accounts)
    if active_number() == int(number):
        set_active_number(accounts[0]["number"] if accounts else None)


def require_active():
    """Helper untuk route: akun aktif atau TelkomselError."""
    account = get_active_account()
    if not account:
        raise TelkomselError("Belum ada akun MyXL yang login atau token sudah "
                             "kedaluwarsa. Tambah akun di menu Akun.")
    return account


def profile_header(account):
    """Data untuk header profil: nomor, tipe, pulsa, masa aktif, poin/tier.
    Setiap panggilan ditoleransi gagal sendiri-sendiri."""
    client = get_client()
    tokens = account["tokens"]
    out = {"number": account["number"],
           "subscription_type": account.get("subscription_type", ""),
           "balance": None, "balance_expired_at": None,
           "points": None, "tier": None, "errors": []}
    try:
        balance = client["engsel"].get_balance(API_KEY, tokens["id_token"])
        if isinstance(balance, dict):
            out["balance"] = balance.get("remaining")
            exp = balance.get("expired_at")
            out["balance_expired_at"] = (
                datetime.fromtimestamp(exp).strftime("%Y-%m-%d") if exp else None)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append("saldo: " + _redact(exc))
    if account.get("subscription_type") == "PREPAID":
        try:
            tiering = client["engsel"].get_tiering_info(API_KEY, tokens)
            if isinstance(tiering, dict):
                out["tier"] = tiering.get("tier")
                out["points"] = tiering.get("current_point")
        except Exception as exc:  # noqa: BLE001
            out["errors"].append("poin: " + _redact(exc))
    return out


# ------------------------------------------------------------------ bookmarks
def load_bookmarks():
    items = _load_json(BOOKMARKS_FILE, [])
    return items if isinstance(items, list) else []


def save_bookmarks(items):
    _save_json(BOOKMARKS_FILE, items)


def add_bookmark(item):
    items = load_bookmarks()
    key = (item["family_code"], item["variant_name"], item["order"])
    if any((p["family_code"], p["variant_name"], p["order"]) == key for p in items):
        return False
    items.append(item)
    save_bookmarks(items)
    return True


def remove_bookmark(family_code, variant_name, order):
    items = [p for p in load_bookmarks()
             if not (p["family_code"] == family_code
                     and p["variant_name"] == variant_name
                     and int(p["order"]) == int(order))]
    save_bookmarks(items)


# ------------------------------------------------------------------ decoy data
def load_decoy(kind):
    """Baca paket decoy dari decoy_data/ (dipakai pembelian dengan decoy).

    kind: 'balance' | 'qris' | 'qris0'. Prefix 'prio-' dipakai untuk tipe
    PRIORITAS/PRIOHYBRID/GO, mengikuti logika DecoyPackage di repo asli.
    """
    account = None
    try:
        account = get_active_account()
    except Exception:  # noqa: BLE001
        account = None
    prefix = "prio-" if account and account.get("subscription_type") in (
        "PRIORITAS", "PRIOHYBRID", "GO") else "default-"
    path = BASE / "decoy_data" / f"decoy-{prefix}{kind}.json"
    if not path.exists():
        path = BASE / "decoy_data" / f"decoy-default-{kind}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def load_hot(kind="hot"):
    path = BASE / "hot_data" / ("hot2.json" if kind == "hot2" else "hot.json")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ purchases
def package_payment_items(client, tokens, option_code):
    """Detail paket + PaymentItem siap bayar (mirip show_package_details)."""
    package = call(client["engsel"].get_package, API_KEY, tokens, option_code)
    if not isinstance(package, dict) or "package_option" not in package:
        raise TelkomselError("Detail paket tidak ditemukan untuk kode itu.")
    option = package["package_option"]
    family = package.get("package_family", {})
    variant = package.get("package_detail_variant", {})
    items = [{"item_code": option_code, "product_type": "",
              "item_price": option["price"],
              "item_name": f"{variant.get('name', '')} {option.get('name', '')}".strip(),
              "tax": 0, "token_confirmation": package["token_confirmation"]}]
    detail = {
        "title": f"{family.get('name', '')} - {variant.get('name', '')} - {option.get('name', '')}".strip(" -"),
        "price": option["price"], "validity": option.get("validity"),
        "point": option.get("point"), "plan_type": family.get("plan_type"),
        "payment_for": family.get("payment_for") or "BUY_PACKAGE",
        "family_code": family.get("package_family_code", ""),
        "family_name": family.get("name", ""), "variant_name": variant.get("name", ""),
        "option_name": option.get("name", ""),
        "token_confirmation": package["token_confirmation"],
        "timestamp": package.get("timestamp"),
        "tnc": client["display_html"](option.get("tnc", "")) if option.get("tnc") else "",
        "benefits": option.get("benefits", []),
        "raw": package,
    }
    return detail, items


def _with_decoy(client, tokens, items, kind, price):
    """Tambah paket decoy ke items; kembalikan (items, total_harga)."""
    decoy = load_decoy(kind)
    decoy_detail = call(client["engsel"].get_package, API_KEY, tokens,
                        decoy["option_code"])
    if not isinstance(decoy_detail, dict):
        raise TelkomselError("Gagal memuat detail paket decoy.")
    decoy_option = decoy_detail["package_option"]
    items.append({"item_code": decoy_detail["package_option"]["package_option_code"],
                  "product_type": "", "item_price": decoy_option["price"],
                  "item_name": decoy_option["name"], "tax": 0,
                  "token_confirmation": decoy_detail["token_confirmation"]})
    return items, price + decoy_option["price"], decoy_detail


def buy_balance(account, option_code, use_decoy=False, decoy_v2=False,
                overwrite_amount=None):
    """Beli dengan pulsa. ask_overwrite=False (web tidak bisa input())."""
    client = get_client()
    tokens = account["tokens"]
    detail, items = package_payment_items(client, tokens, option_code)
    token_idx = 0
    amount = overwrite_amount if overwrite_amount is not None else detail["price"]
    if use_decoy:
        items, amount, _ = _with_decoy(client, tokens, items, "balance", detail["price"])
        token_idx = 1 if decoy_v2 else 0
        if decoy_v2:
            amount = amount  # total tetap harga paket + decoy
    result = call(client["purchase_balance"].settlement_balance,
                  API_KEY, tokens, items, detail["payment_for"],
                  False, overwrite_amount=amount,
                  token_confirmation_idx=token_idx)
    return {"detail": detail, "result": result}


def buy_qris(account, option_code, use_decoy=False, decoy_kind="qris"):
    """Beli dengan QRIS. Mengembalikan transaction_id + string QR + gambar QR (base64 PNG)."""
    client = get_client()
    tokens = account["tokens"]
    detail, items = package_payment_items(client, tokens, option_code)
    token_idx = 0
    amount = detail["price"]
    payment_for = detail["payment_for"]
    if use_decoy:
        items, amount, _ = _with_decoy(client, tokens, items, decoy_kind, detail["price"])
        token_idx = 1
        payment_for = "SHARE_PACKAGE"
    tx_id = call(client["purchase_qris"].settlement_qris,
                 API_KEY, tokens, items, payment_for,
                 False, overwrite_amount=amount,
                 token_confirmation_idx=token_idx)
    if not tx_id or not isinstance(tx_id, str):
        raise TelkomselError("Gagal membuat transaksi QRIS: " + _redact(tx_id))
    qr_string = call(client["purchase_qris"].get_qris_code, API_KEY, tokens, tx_id)
    qr_png = None
    if qr_string:
        import qrcode
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L,
                           box_size=6, border=2)
        qr.add_data(qr_string)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        qr_png = base64.b64encode(buf.getvalue()).decode()
    return {"detail": detail, "transaction_id": tx_id,
            "qr_string": qr_string, "qr_png": qr_png,
            "qris_url": ("https://ki-ar-kod.netlify.app/?data="
                         + base64.urlsafe_b64encode(str(qr_string).encode()).decode()) if qr_string else None}


def buy_ewallet(account, option_code, method, wallet_number=""):
    """Beli dengan e-wallet. method: DANA | SHOPEEPAY | GOPAY | OVO."""
    client = get_client()
    tokens = account["tokens"]
    detail, items = package_payment_items(client, tokens, option_code)
    result = call(client["purchase_ewallet"].settlement_multipayment,
                  API_KEY, tokens, items, wallet_number, method,
                  detail["payment_for"], False,
                  overwrite_amount=detail["price"])
    deeplink = None
    if isinstance(result, dict):
        deeplink = result.get("data", {}).get("deeplink")
    return {"detail": detail, "result": result, "deeplink": deeplink}


def buy_redeem(account, option_code, mode, destination_msisdn=""):
    """Ambil sebagai bonus (bounty) atau beli dengan poin (loyalty).

    mode: 'bounty' | 'bounty_allotment' | 'loyalty' — mengikuti
    show_package_details di repo asli. bounty_allotment butuh nomor
    tujuan (destination_msisdn).
    """
    client = get_client()
    tokens = account["tokens"]
    detail, items = package_payment_items(client, tokens, option_code)
    redeem = client["purchase_redeem"]
    if mode == "bounty":
        result = call(redeem.settlement_bounty, API_KEY, tokens,
                      detail["token_confirmation"], detail["timestamp"],
                      option_code, detail["price"], detail["title"])
    elif mode == "bounty_allotment":
        if not destination_msisdn:
            raise TelkomselError("Kirim bonus butuh nomor tujuan (destination_msisdn).")
        result = call(redeem.bounty_allotment, API_KEY, tokens,
                      detail["timestamp"], destination_msisdn,
                      detail["title"], option_code, detail["token_confirmation"])
    elif mode == "loyalty":
        result = call(redeem.settlement_loyalty, API_KEY, tokens,
                      detail["token_confirmation"], detail["timestamp"],
                      option_code, detail["price"])
    else:
        raise TelkomselError("Mode redeem tidak dikenal.")
    return {"detail": detail, "result": result}


def buy_n_times(account, family_code, variant_code, option_order, n,
                use_decoy=False, delay=0, job=None):
    """Beli satu opsi paket sebanyak n kali (mirip purchase_n_times).

    Dijalankan di thread job agar halaman tidak menggantung; progres
    ditulis ke job (dict) yang dipoll halaman.
    """
    client = get_client()
    tokens = account["tokens"]
    family = call(client["engsel"].get_family, API_KEY, tokens, family_code)
    variants = family.get("package_variants", []) if isinstance(family, dict) else []
    target_variant = next((v for v in variants
                           if v.get("package_variant_code") == variant_code), None)
    if not target_variant:
        raise TelkomselError("Variant tidak ditemukan di family itu.")
    target_option = next((o for o in target_variant.get("package_options", [])
                          if o.get("order") == option_order), None)
    if not target_option:
        raise TelkomselError("Option order tidak ditemukan di variant itu.")
    successes = []
    for i in range(1, n + 1):
        if job is not None:
            job["log"].append(f"Pembelian {i}/{n}: {target_variant['name']} - "
                               f"{target_option['name']} - Rp{target_option['price']}")
            job["progress"] = i - 1
        tokens = require_active()["tokens"]
        detail = call(client["engsel"].get_package_details, API_KEY, tokens,
                      family_code, variant_code, option_order, None, None)
        items = [{"item_code": detail["package_option"]["package_option_code"],
                  "product_type": "", "item_price": detail["package_option"]["price"],
                  "item_name": detail["package_option"]["name"], "tax": 0,
                  "token_confirmation": detail["token_confirmation"]}]
        amount = detail["package_option"]["price"]
        if use_decoy:
            items, amount, _ = _with_decoy(client, tokens, items, "balance", amount)
        result = call(client["purchase_balance"].settlement_balance,
                      API_KEY, tokens, items, "BUY_PACKAGE", False,
                      overwrite_amount=amount)
        ok = isinstance(result, dict) and result.get("status") == "SUCCESS"
        successes.append({"n": i, "success": ok, "result": result})
        if job is not None:
            job["log"].append(("Berhasil." if ok else "Gagal: ")
                              + _redact(result if not ok else ""))
            job["progress"] = i
        if delay and i < n:
            time.sleep(delay)
    return successes


def buy_family_loop(account, family_code, use_decoy=False, delay=0,
                    start_from_option=1, job=None):
    """Beli SEMUA paket dalam satu family, satu per satu (mirip
    purchase_by_family). Berjalan di thread job; halaman mem-poll progres."""
    client = get_client()
    tokens = account["tokens"]
    if use_decoy:
        decoy = load_decoy("balance")
        decoy_detail = call(client["engsel"].get_package, API_KEY, tokens,
                            decoy["option_code"])
        threshold = decoy_detail["package_option"]["price"]
        if job is not None:
            job["log"].append(
                f"Mode decoy: pastikan sisa pulsa KURANG DARI Rp{threshold}!")
    family = call(client["engsel"].get_family, API_KEY, tokens, family_code)
    if not isinstance(family, dict):
        raise TelkomselError("Gagal memuat data family.")
    family_name = family.get("package_family", {}).get("name", family_code)
    variants = family.get("package_variants", [])
    total = sum(len(v.get("package_options", [])) for v in variants)
    if job is not None:
        job["log"].append(f"Family: {family_name} — {total} opsi paket.")
    results = []
    count = 0
    started = start_from_option <= 1
    for variant in variants:
        for option in variant.get("package_options", []):
            if not started and option.get("order") == start_from_option:
                started = True
            if not started:
                continue
            count += 1
            tokens = require_active()["tokens"]
            label = f"{variant.get('name')} - {option.get('order')}. {option.get('name')} - Rp{option.get('price')}"
            if job is not None:
                job["log"].append(f"[{count}/{total}] Membeli: {label}")
                job["progress"] = count - 1
            try:
                detail = call(client["engsel"].get_package_details, API_KEY, tokens,
                              family_code, variant.get("package_variant_code"),
                              option.get("order"), None, None)
                items = [{"item_code": detail["package_option"]["package_option_code"],
                          "product_type": "", "item_price": detail["package_option"]["price"],
                          "item_name": detail["package_option"]["name"], "tax": 0,
                          "token_confirmation": detail["token_confirmation"]}]
                amount = detail["package_option"]["price"]
                if use_decoy:
                    items, amount, _ = _with_decoy(client, tokens, items, "balance", amount)
                result = call(client["purchase_balance"].settlement_balance,
                              API_KEY, tokens, items, "BUY_PACKAGE", False,
                              overwrite_amount=amount)
                ok = isinstance(result, dict) and result.get("status") == "SUCCESS"
            except Exception as exc:  # noqa: BLE001
                ok, result = False, {"error": _redact(exc)}
            results.append({"option": label, "success": ok, "result": result})
            if job is not None:
                job["log"].append(("  -> Berhasil." if ok else "  -> Gagal."))
                job["progress"] = count
            if delay and count < total:
                time.sleep(delay)
    if job is not None:
        job["log"].append(
            f"Selesai: {sum(1 for r in results if r['success'])}/{len(results)} berhasil.")
    return results


# ------------------------------------------------------------------ background jobs
JOBS = {}
JOBS_LOCK = threading.Lock()


def start_job(kind, fn, *args, **kwargs):
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "kind": kind, "status": "running",
           "progress": 0, "log": [], "result": None, "error": None,
           "started_at": datetime.now().strftime("%H:%M:%S")}
    with JOBS_LOCK:
        JOBS[job_id] = job

    def _run():
        try:
            job["result"] = fn(job=job, *args, **kwargs)
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = _redact(exc)
            job["log"].append("Gagal: " + _redact(exc))

    threading.Thread(target=_run, daemon=True).start()
    return job


def get_job(job_id):
    with JOBS_LOCK:
        return JOBS.get(job_id)


def format_rupiah(value):
    try:
        return f"Rp{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)
