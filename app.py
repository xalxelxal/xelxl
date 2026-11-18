"""MYnyak Engsel Sunset — versi Web.

Menjalankan:
    python app.py
Lalu buka http://127.0.0.1:5000

Login web app (bukan login Telkomsel): password diatur lewat env
WEB_PASSWORD. Kalau belum diatur, app membuat password acak saat pertama
kali jalan, mencetaknya SEKALI ke terminal, dan menyimpannya di
data/.web_password (chmod 600) supaya tidak terkunci setelah restart.
"""
import os
import secrets
import sys
from functools import wraps
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)  # modul client menulis ax.fp di working directory

from flask import (  # noqa: E402
    Flask, flash, jsonify, redirect, render_template, request, session, url_for,
)

import telkomsel as tk  # noqa: E402
from app.menus.util import format_quota_byte as _format_quota_byte  # noqa: E402

app = Flask(__name__)
app.jinja_env.filters["rupiah"] = tk.format_rupiah
app.jinja_env.filters["quota"] = lambda v: _format_quota_byte(int(v)) if isinstance(v, (int, float)) else v

# --- secret key Flask (session) -------------------------------------------
_secret_file = tk.DATA / ".flask_secret"
if _secret_file.exists():
    app.secret_key = _secret_file.read_text(encoding="utf-8").strip()
else:
    app.secret_key = secrets.token_hex(32)
    _secret_file.write_text(app.secret_key, encoding="utf-8")
    os.chmod(_secret_file, 0o600)

# --- password web app ------------------------------------------------------
_pw_file = tk.DATA / ".web_password"
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "").strip()
if WEB_PASSWORD:
    _pw_source = "environment WEB_PASSWORD"
else:
    if _pw_file.exists():
        WEB_PASSWORD = _pw_file.read_text(encoding="utf-8").strip()
        _pw_source = "file data/.web_password"
    else:
        WEB_PASSWORD = secrets.token_urlsafe(16)
        _pw_file.write_text(WEB_PASSWORD, encoding="utf-8")
        os.chmod(_pw_file, 0o600)
        _pw_source = "dibuat acak (lihat terminal saat start pertama)"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("web_logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _ctx(**extra):
    """Konteks umum semua halaman: status env + profil aktif (best effort)."""
    ctx = {"env": tk.env_status(), "profile": None, "account": None,
           "accounts": tk.load_accounts(), "active_number": tk.active_number()}
    try:
        account = tk.get_active_account()
        if account:
            ctx["account"] = account
            ctx["profile"] = tk.profile_header(account)
    except Exception:  # noqa: BLE001 - dashboard tetap tampil tanpa profil
        pass
    ctx.update(extra)
    return ctx


def _safe(fn, *args, **kwargs):
    """Jalankan pemanggilan API; kalau gagal, flash pesannya dan kembalikan None."""
    try:
        return fn(*args, **kwargs)
    except tk.TelkomselError as exc:
        flash(str(exc), "error")
    except Exception as exc:  # noqa: BLE001
        flash("Terjadi kesalahan: " + tk._redact(exc), "error")
    return None


# ------------------------------------------------------------------ web login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password", "") == WEB_PASSWORD:
            session["web_logged_in"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Password salah.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ dashboard
@app.route("/")
@login_required
def dashboard():
    ctx = _ctx()
    packages = None
    if ctx["account"]:
        client = _safe(tk.get_client)
        if client:
            packages = _safe(_my_packages, client, ctx["account"])
    return render_template("dashboard.html", packages=packages, **ctx)


def _my_packages(client, account):
    """Paket Saya (fetch_my_packages di CLI): quota-details."""
    tokens = account["tokens"]
    res = tk.call(client["engsel"].send_api_request, tk.API_KEY,
                  "api/v8/packages/quota-details",
                  {"is_enterprise": False, "lang": "en"}, tokens["id_token"], "POST")
    if isinstance(res, dict):
        return res.get("data", res)
    return res


@app.route("/paket-saya")
@login_required
def paket_saya():
    ctx = _ctx()
    packages = None
    if ctx["account"]:
        client = _safe(tk.get_client)
        if client:
            packages = _safe(_my_packages, client, ctx["account"])
    return render_template("paket_saya.html", packages=packages, **ctx)


# ------------------------------------------------------------------ akun (OTP)
@app.route("/akun", methods=["GET", "POST"])
@login_required
def akun():
    ctx = _ctx()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "request_otp":
            number = request.form.get("number", "").strip()
            if not number.startswith("628") or not (10 <= len(number) <= 14):
                flash("Nomor tidak valid. Awali dengan 628, panjang 10-14 digit.", "error")
            else:
                client = _safe(tk.get_client)
                if client:
                    sub_id = _safe(client["ciam"].get_otp, number)
                    if sub_id:
                        session["otp_number"] = number
                        session["otp_subscriber_id"] = sub_id
                        flash("OTP dikirim ke nomor kamu. Masukkan kode 6 digit di bawah.", "ok")
        elif action == "submit_otp":
            number = session.get("otp_number", "")
            code = request.form.get("otp", "").strip()
            if not number or len(code) != 6 or not code.isdigit():
                flash("OTP harus 6 digit angka.", "error")
            else:
                client = _safe(tk.get_client)
                if client:
                    tokens = _safe(client["ciam"].submit_otp, tk.API_KEY, "SMS", number, code)
                    if tokens and isinstance(tokens, dict) and tokens.get("refresh_token"):
                        _safe(tk.add_account, int(number), tokens["refresh_token"])
                        session.pop("otp_number", None)
                        session.pop("otp_subscriber_id", None)
                        flash(f"Berhasil login sebagai {number}.", "ok")
                        return redirect(url_for("akun"))
                    flash("OTP salah atau sudah kedaluwarsa.", "error")
        elif action == "switch":
            number = request.form.get("number", "")
            if number.isdigit():
                tk.set_active_number(int(number))
                flash(f"Akun aktif diganti ke {number}.", "ok")
                return redirect(url_for("dashboard"))
        elif action == "delete":
            number = request.form.get("number", "")
            if number.isdigit():
                tk.remove_account(int(number))
                flash(f"Akun {number} dihapus.", "ok")
                return redirect(url_for("akun"))
    return render_template("akun.html", otp_number=session.get("otp_number"), **ctx)


# ------------------------------------------------------------------ paket HOT
@app.route("/hot")
@login_required
def hot():
    ctx = _ctx()
    return render_template("hot.html", items=tk.load_hot("hot"), kind="hot", **ctx)


@app.route("/hot2")
@login_required
def hot2():
    ctx = _ctx()
    return render_template("hot.html", items=tk.load_hot("hot2"), kind="hot2", **ctx)


@app.route("/hot/beli", methods=["POST"])
@login_required
def hot_beli():
    """HOT (menu 3): pilih paket dari hot.json -> cari option_code-nya
    lewat family, lalu tampilkan detail paket."""
    ctx = _ctx()
    try:
        idx = int(request.form.get("index", "-1"))
        items = tk.load_hot("hot")
        selected = items[idx]
    except (ValueError, IndexError):
        flash("Paket HOT tidak ditemukan.", "error")
        return redirect(url_for("hot"))
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if not account or not client:
        return redirect(url_for("hot"))
    family = _safe(client["engsel"].get_family, tk.API_KEY, account["tokens"],
                   selected["family_code"], selected.get("is_enterprise"))
    if not family:
        return redirect(url_for("hot"))
    option_code = None
    for variant in family.get("package_variants", []):
        if variant.get("name") == selected["variant_name"]:
            for option in variant.get("package_options", []):
                if option.get("order") == selected["order"]:
                    option_code = option.get("package_option_code")
    if not option_code:
        flash("Option paket tidak ditemukan di family itu (mungkin sudah berubah).", "error")
        return redirect(url_for("hot"))
    return redirect(url_for("paket_detail", option_code=option_code,
                            is_enterprise=int(bool(selected.get("is_enterprise"))),
                            option_order=selected["order"]))


@app.route("/hot2/beli", methods=["POST"])
@login_required
def hot2_beli():
    """HOT-2 (menu 4): satu entri berisi beberapa paket sekaligus.
    Dibuatkan halaman detail gabungan; pembelian dilakukan per paket."""
    ctx = _ctx()
    try:
        idx = int(request.form.get("index", "-1"))
        items = tk.load_hot("hot2")
        selected = items[idx]
    except (ValueError, IndexError):
        flash("Paket HOT-2 tidak ditemukan.", "error")
        return redirect(url_for("hot2"))
    return render_template("hot2.html", item=selected, **ctx)


# ------------------------------------------------------------------ detail & beli
@app.route("/paket/<option_code>")
@login_required
def paket_detail(option_code):
    ctx = _ctx()
    is_enterprise = request.args.get("is_enterprise", "0") == "1"
    option_order = int(request.args.get("option_order", "-1"))
    detail = items = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    variant_code = ""
    if account and client:
        detail, items = _safe(tk.package_payment_items, client, account["tokens"], option_code) or (None, None)
        addons = _safe(client["engsel"].get_addons, tk.API_KEY, account["tokens"], option_code)
        # Cari variant_code untuk form "Beli N kali" (butuh family + variant + order)
        if detail and option_order != -1:
            family = _safe(client["engsel"].get_family, tk.API_KEY, account["tokens"],
                           detail["family_code"], is_enterprise)
            if isinstance(family, dict):
                for variant in family.get("package_variants", []):
                    if any(o.get("order") == option_order
                           for o in variant.get("package_options", [])):
                        variant_code = variant.get("package_variant_code", "")
                        break
    else:
        addons = None
    return render_template("paket_detail.html", detail=detail, items=items,
                           option_code=option_code, is_enterprise=is_enterprise,
                           option_order=option_order, addons=addons,
                           variant_code=variant_code, **ctx)


@app.route("/paket/<option_code>/beli", methods=["POST"])
@login_required
def paket_beli(option_code):
    method = request.form.get("method", "")
    account = _safe(tk.require_active)
    if not account:
        return redirect(url_for("paket_detail", option_code=option_code))
    try:
        if method == "balance":
            out = _safe(tk.buy_balance, account, option_code)
            return render_template("hasil_beli.html", method="Pulsa", out=out, **_ctx())
        if method == "balance_decoy":
            out = _safe(tk.buy_balance, account, option_code, use_decoy=True)
            return render_template("hasil_beli.html", method="Pulsa + Decoy", out=out, **_ctx())
        if method == "balance_decoy_v2":
            out = _safe(tk.buy_balance, account, option_code, use_decoy=True, decoy_v2=True)
            return render_template("hasil_beli.html", method="Pulsa + Decoy V2", out=out, **_ctx())
        if method == "qris":
            out = _safe(tk.buy_qris, account, option_code)
            return render_template("hasil_beli.html", method="QRIS", out=out, **_ctx())
        if method == "qris_decoy":
            out = _safe(tk.buy_qris, account, option_code, use_decoy=True, decoy_kind="qris")
            return render_template("hasil_beli.html", method="QRIS + Decoy", out=out, **_ctx())
        if method == "qris_decoy0":
            out = _safe(tk.buy_qris, account, option_code, use_decoy=True, decoy_kind="qris0")
            return render_template("hasil_beli.html", method="QRIS + Decoy V2", out=out, **_ctx())
        if method == "ewallet":
            wallet_method = request.form.get("wallet_method", "DANA")
            wallet_number = request.form.get("wallet_number", "").strip()
            if wallet_method in ("DANA", "OVO") and not wallet_number:
                flash("Nomor e-wallet wajib diisi untuk DANA/OVO.", "error")
                return redirect(url_for("paket_detail", option_code=option_code))
            out = _safe(tk.buy_ewallet, account, option_code, wallet_method, wallet_number)
            return render_template("hasil_beli.html", method=f"E-Wallet ({wallet_method})", out=out, **_ctx())
        if method in ("bounty", "bounty_allotment", "loyalty"):
            label = {"bounty": "Ambil sebagai bonus", "bounty_allotment": "Kirim bonus",
                     "loyalty": "Beli dengan Poin"}[method]
            out = _safe(tk.buy_redeem, account, option_code, method,
                        request.form.get("destination_msisdn", "").strip())
            return render_template("hasil_beli.html", method=label, out=out, **_ctx())
        if method == "buy_n":
            n = int(request.form.get("n", "1"))
            family_code = request.form.get("family_code", "")
            variant_code = request.form.get("variant_code", "")
            option_order = int(request.form.get("option_order", "0"))
            use_decoy = request.form.get("use_decoy") == "1"
            delay = int(request.form.get("delay", "0") or 0)
            job = tk.start_job("buy_n", tk.buy_n_times, account, family_code,
                               variant_code, option_order, n, use_decoy, delay)
            return redirect(url_for("job_status", job_id=job["id"]))
        flash("Metode pembayaran tidak dikenal.", "error")
    except ValueError:
        flash("Jumlah pembelian tidak valid.", "error")
    return redirect(url_for("paket_detail", option_code=option_code))


@app.route("/paket/<option_code>/bookmark", methods=["POST"])
@login_required
def paket_bookmark(option_code):
    item = {"family_code": request.form.get("family_code", ""),
            "family_name": request.form.get("family_name", ""),
            "is_enterprise": request.form.get("is_enterprise") == "1",
            "variant_name": request.form.get("variant_name", ""),
            "option_name": request.form.get("option_name", ""),
            "order": int(request.form.get("option_order", "0") or 0)}
    if tk.add_bookmark(item):
        flash("Paket ditambahkan ke bookmark.", "ok")
    else:
        flash("Paket sudah ada di bookmark.", "error")
    return redirect(url_for("paket_detail", option_code=option_code,
                            option_order=item["order"]))


@app.route("/paket/<option_code>/berhenti", methods=["POST"])
@login_required
def paket_berhenti(option_code):
    """Berhenti berlangganan. API unsubscribe() butuh quota_code,
    product_domain, dan product_subscription_type — ketiganya diambil
    dari data paket (form di halaman Paket Saya)."""
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    quota_code = request.form.get("quota_code", "").strip() or option_code
    product_domain = request.form.get("product_domain", "").strip()
    product_sub_type = request.form.get("product_subscription_type", "").strip()
    if account and client:
        if not product_domain or not product_sub_type:
            flash("Berhenti berlangganan butuh product_domain dan "
                  "product_subscription_type dari data paket. Buka halaman "
                  "Paket Saya dan gunakan tombol di sana.", "error")
        else:
            res = _safe(client["engsel"].unsubscribe, tk.API_KEY, account["tokens"],
                        quota_code, product_domain, product_sub_type)
            if res:
                flash("Berhasil berhenti berlangganan.", "ok")
            elif res is False:
                flash("Gagal berhenti berlangganan (server menolak).", "error")
    return redirect(url_for("paket_saya"))


# ------------------------------------------------------------------ beli by code
@app.route("/beli-kode", methods=["GET", "POST"])
@login_required
def beli_kode():
    ctx = _ctx()
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if not code:
            flash("Kode paket wajib diisi.", "error")
        else:
            return redirect(url_for("paket_detail", option_code=code))
    return render_template("beli_kode.html", **ctx)


@app.route("/beli-family", methods=["GET", "POST"])
@login_required
def beli_family():
    ctx = _ctx()
    family = None
    if request.method == "POST":
        family_code = request.form.get("family_code", "").strip()
        is_enterprise = request.form.get("is_enterprise") == "1"
        migration_type = request.form.get("migration_type") or None
        account = _safe(tk.require_active)
        client = _safe(tk.get_client)
        if account and client and family_code:
            family = _safe(client["engsel"].get_family, tk.API_KEY, account["tokens"],
                           family_code, is_enterprise, migration_type)
            if family:
                session["last_family_code"] = family_code
    return render_template("beli_family.html", family=family, **ctx)


@app.route("/beli-family/loop", methods=["POST"])
@login_required
def beli_family_loop():
    account = _safe(tk.require_active)
    if not account:
        return redirect(url_for("beli_family"))
    family_code = request.form.get("family_code", "").strip()
    use_decoy = request.form.get("use_decoy") == "1"
    delay = int(request.form.get("delay", "0") or 0)
    start_from = int(request.form.get("start_from_option", "1") or 1)
    if not family_code:
        flash("Family code wajib diisi.", "error")
        return redirect(url_for("beli_family"))
    job = tk.start_job("buy_family_loop", tk.buy_family_loop, account,
                       family_code, use_decoy, delay, start_from)
    flash("Pembelian loop dimulai di latar belakang. Halaman ini menampilkan progresnya.", "ok")
    return redirect(url_for("job_status", job_id=job["id"]))


@app.route("/job/<job_id>")
@login_required
def job_status(job_id):
    job = tk.get_job(job_id)
    if not job:
        flash("Job tidak ditemukan.", "error")
        return redirect(url_for("dashboard"))
    return render_template("job.html", job=job, **_ctx())


@app.route("/api/job/<job_id>")
@login_required
def api_job(job_id):
    job = tk.get_job(job_id)
    if not job:
        return jsonify({"error": "tidak ditemukan"}), 404
    return jsonify(job)


# ------------------------------------------------------------------ riwayat
@app.route("/riwayat")
@login_required
def riwayat():
    ctx = _ctx()
    history = pending = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        history = _safe(client["engsel"].get_transaction_history, tk.API_KEY, account["tokens"])
        pending = _safe(client["engsel"].get_pending_transaction, tk.API_KEY, account["tokens"])
    return render_template("riwayat.html", history=history, pending=pending, **ctx)


# ------------------------------------------------------------------ akrab / famplan
@app.route("/akrab", methods=["GET", "POST"])
@login_required
def akrab():
    ctx = _ctx()
    data = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        if request.method == "POST":
            action = request.form.get("action", "")
            if action == "change_member":
                res = _safe(client["famplan"].change_member, tk.API_KEY, account["tokens"],
                            request.form.get("parent_alias", ""),
                            request.form.get("alias", ""),
                            int(request.form.get("slot_id", "0") or 0),
                            request.form.get("family_member_id", ""),
                            request.form.get("new_msisdn", ""))
                if res is not None:
                    flash("Permintaan ganti anggota dikirim.", "ok")
            elif action == "remove_member":
                res = _safe(client["famplan"].remove_member, tk.API_KEY, account["tokens"],
                            request.form.get("family_member_id", ""))
                if res is not None:
                    flash("Permintaan hapus anggota dikirim.", "ok")
            elif action == "set_quota":
                res = _safe(client["famplan"].set_quota_limit, tk.API_KEY, account["tokens"],
                            int(request.form.get("original_allocation", "0") or 0),
                            int(request.form.get("new_allocation", "0") or 0),
                            request.form.get("family_member_id", ""))
                if res is not None:
                    flash("Permintaan ubah kuota dikirim.", "ok")
        data = _safe(client["famplan"].get_family_data, tk.API_KEY, account["tokens"])
    return render_template("akrab.html", data=data, **ctx)


# ------------------------------------------------------------------ circle
def _find_in(data, *keys):
    """Cari nilai key di dict bersarang (group_id, parent_subs_id, ...)."""
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key]:
                return data[key]
        for value in data.values():
            found = _find_in(value, *keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_in(item, *keys)
            if found:
                return found
    return None


@app.route("/circle", methods=["GET", "POST"])
@login_required
def circle():
    ctx = _ctx()
    group = members = bonus = None
    group_id = parent_subs_id = family_id = member_id_parent = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        group = _safe(client["circle"].get_group_data, tk.API_KEY, account["tokens"])
        group_id = _find_in(group, "group_id")
        parent_subs_id = _find_in(group, "parent_subs_id", "parent_subscriber_id")
        family_id = _find_in(group, "family_id")
        member_id_parent = _find_in(group, "member_id_parent")
        if request.method == "POST":
            action = request.form.get("action", "")
            msisdn = request.form.get("msisdn", "").strip()
            name = request.form.get("name", "").strip()
            member_id = request.form.get("member_id", "").strip()
            gid = request.form.get("group_id", "").strip() or (str(group_id) if group_id else "")
            if action == "create":
                res = _safe(client["circle"].create_circle, tk.API_KEY, account["tokens"],
                            request.form.get("parent_name", "").strip(),
                            request.form.get("group_name", "").strip(),
                            msisdn, name)
                if res is not None:
                    flash("Permintaan buat Circle dikirim.", "ok")
            elif action == "invite" and msisdn and gid:
                res = _safe(client["circle"].invite_circle_member, tk.API_KEY, account["tokens"],
                            msisdn, name, gid, member_id_parent or "")
                if res is not None:
                    flash("Undangan Circle dikirim.", "ok")
            elif action == "remove" and member_id and gid:
                res = _safe(client["circle"].remove_circle_member, tk.API_KEY, account["tokens"],
                            member_id, gid, member_id_parent or "",
                            request.form.get("is_last_member") == "1")
                if res is not None:
                    flash("Permintaan hapus anggota Circle dikirim.", "ok")
            elif action == "accept" and gid and member_id:
                res = _safe(client["circle"].accept_circle_invitation, tk.API_KEY, account["tokens"],
                            gid, member_id)
                if res is not None:
                    flash("Undangan Circle diterima.", "ok")
            elif action == "validate" and msisdn:
                res = _safe(client["circle"].validate_circle_member, tk.API_KEY, account["tokens"], msisdn)
                if res is not None:
                    flash("Hasil validasi: " + str(res)[:300], "ok")
            # muat ulang data setelah aksi
            group = _safe(client["circle"].get_group_data, tk.API_KEY, account["tokens"])
            group_id = _find_in(group, "group_id")
            parent_subs_id = _find_in(group, "parent_subs_id", "parent_subscriber_id")
            family_id = _find_in(group, "family_id")
        if group_id:
            members = _safe(client["circle"].get_group_members, tk.API_KEY, account["tokens"], str(group_id))
        if parent_subs_id and family_id:
            bonus = _safe(client["circle"].get_bonus_data, tk.API_KEY, account["tokens"],
                          str(parent_subs_id), str(family_id))
    return render_template("circle.html", group=group, members=members, bonus=bonus,
                           group_id=group_id, parent_subs_id=parent_subs_id,
                           family_id=family_id, **ctx)


@app.route("/circle/spending")
@login_required
def circle_spending():
    ctx = _ctx()
    data = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        group = _safe(client["circle"].get_group_data, tk.API_KEY, account["tokens"])
        parent_subs_id = _find_in(group, "parent_subs_id", "parent_subscriber_id")
        family_id = _find_in(group, "family_id")
        if parent_subs_id and family_id:
            data = _safe(client["circle"].spending_tracker, tk.API_KEY, account["tokens"],
                         str(parent_subs_id), str(family_id))
        else:
            flash("Spending tracker butuh parent_subs_id dan family_id dari data Circle — tidak ditemukan.", "error")
    return render_template("generic.html", title="Spending Tracker Circle",
                           data=data, back=url_for("circle"), **ctx)


# ------------------------------------------------------------------ store
@app.route("/store/segments", methods=["GET", "POST"])
@login_required
def store_segments():
    ctx = _ctx()
    data = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        is_enterprise = request.form.get("is_enterprise") == "1" if request.method == "POST" else False
        data = _safe(client["store_segments"].get_segments, tk.API_KEY, account["tokens"], is_enterprise)
    return render_template("store_segments.html", data=data, **ctx)


@app.route("/store/family", methods=["GET", "POST"])
@login_required
def store_family():
    ctx = _ctx()
    data = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        subs_type = request.form.get("subs_type", "PREPAID") if request.method == "POST" else "PREPAID"
        is_enterprise = request.form.get("is_enterprise") == "1" if request.method == "POST" else False
        data = _safe(client["store_search"].get_family_list, tk.API_KEY, account["tokens"],
                     subs_type, is_enterprise)
    return render_template("store_family.html", data=data, **ctx)


@app.route("/store/packages", methods=["GET", "POST"])
@login_required
def store_packages():
    ctx = _ctx()
    data = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        subs_type = request.form.get("subs_type", "PREPAID") if request.method == "POST" else "PREPAID"
        is_enterprise = request.form.get("is_enterprise") == "1" if request.method == "POST" else False
        data = _safe(client["store_search"].get_store_packages, tk.API_KEY, account["tokens"],
                     subs_type, is_enterprise)
    return render_template("store_packages.html", data=data, **ctx)


@app.route("/store/redeemables", methods=["GET", "POST"])
@login_required
def store_redeemables():
    ctx = _ctx()
    data = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        is_enterprise = request.form.get("is_enterprise") == "1" if request.method == "POST" else False
        data = _safe(client["store_redeemables"].get_redeemables, tk.API_KEY, account["tokens"], is_enterprise)
    return render_template("generic.html", title="Redeemables", data=data,
                           back=url_for("store_redeemables"), **ctx)


# ------------------------------------------------------------------ registrasi
@app.route("/registrasi", methods=["GET", "POST"])
@login_required
def registrasi():
    ctx = _ctx()
    result = None
    if request.method == "POST":
        action = request.form.get("action", "")
        client = _safe(tk.get_client)
        if client:
            msisdn = request.form.get("msisdn", "").strip()
            if action == "dukcapil":
                result = _safe(client["registration"].dukcapil, tk.API_KEY, msisdn,
                               request.form.get("kk", "").strip(),
                               request.form.get("nik", "").strip())
                if result is not None:
                    flash("Permintaan registrasi Dukcapil dikirim.", "ok")
            elif action == "validate_puk":
                result = _safe(client["registration"].validate_puk, tk.API_KEY, msisdn,
                               request.form.get("puk", "").strip())
            elif action == "validate_msisdn":
                account = _safe(tk.require_active)
                if account:
                    result = _safe(client["famplan"].validate_msisdn, tk.API_KEY,
                                   account["tokens"], msisdn)
    return render_template("registrasi.html", result=result, **ctx)


@app.route("/validasi", methods=["GET", "POST"])
@login_required
def validasi():
    ctx = _ctx()
    result = None
    if request.method == "POST":
        msisdn = request.form.get("msisdn", "").strip()
        account = _safe(tk.require_active)
        client = _safe(tk.get_client)
        if account and client and msisdn:
            result = _safe(client["famplan"].validate_msisdn, tk.API_KEY, account["tokens"], msisdn)
    return render_template("generic.html", title="Validasi Nomor (MSISDN)",
                           data=result, back=url_for("validasi"), **ctx)


# ------------------------------------------------------------------ notifikasi
@app.route("/notifikasi", methods=["GET", "POST"])
@login_required
def notifikasi():
    ctx = _ctx()
    data = detail = None
    account = _safe(tk.require_active)
    client = _safe(tk.get_client)
    if account and client:
        if request.method == "POST" and request.form.get("notification_id"):
            detail = _safe(client["engsel"].get_notification_detail, tk.API_KEY,
                           account["tokens"], request.form.get("notification_id"))
        data = _safe(client["engsel"].dashboard_segments, tk.API_KEY, account["tokens"])
        if isinstance(data, dict):
            data = data.get("data", {}).get("notification", {}).get("data", data)
    return render_template("notifikasi.html", data=data, detail=detail, **ctx)


# ------------------------------------------------------------------ bookmark
@app.route("/bookmark", methods=["GET", "POST"])
@login_required
def bookmark():
    ctx = _ctx()
    if request.method == "POST":
        if request.form.get("action") == "delete":
            tk.remove_bookmark(request.form.get("family_code", ""),
                               request.form.get("variant_name", ""),
                               int(request.form.get("order", "0") or 0))
            flash("Bookmark dihapus.", "ok")
            return redirect(url_for("bookmark"))
    return render_template("bookmark.html", items=tk.load_bookmarks(), **ctx)


# ------------------------------------------------------------------ pengaturan
@app.route("/pengaturan", methods=["GET", "POST"])
@login_required
def pengaturan():
    status = tk.env_status()
    if request.method == "POST":
        values = {key: request.form.get(key, "") for key in tk.ENV_KEY_NAMES}
        status = tk.save_env(values)
        flash("Pengaturan .env disimpan. Modul API dimuat ulang.", "ok")
        return redirect(url_for("pengaturan"))
    current = tk.parse_env_file()
    return render_template("pengaturan.html", status=status, current=current,
                           pw_source=_pw_source, **_ctx())


@app.route("/kesehatan")
@login_required
def kesehatan():
    """Cek cepat: apakah .env lengkap dan modul client bisa diimport."""
    status = tk.env_status()
    try:
        tk.get_client()
        client_ok, client_error = True, None
    except tk.TelkomselError as exc:
        client_ok, client_error = False, str(exc)
    return jsonify({"env_ready": status["ready"],
                    "missing_required": status["missing_required"],
                    "client_ok": client_ok, "client_error": client_error,
                    "accounts": len(tk.load_accounts()),
                    "active_number": tk.active_number()})


@app.errorhandler(404)
def not_found(_exc):
    return render_template("generic.html", title="Halaman tidak ditemukan",
                           data=None, back=url_for("dashboard"), **_ctx()), 404


if __name__ == "__main__":
    print("=" * 60)
    print("MYnyak Engsel Sunset — versi Web")
    print("Buka: http://127.0.0.1:5000")
    print(f"Password web app: {WEB_PASSWORD}  (sumber: {_pw_source})")
    print("SIMPAN password itu. Jangan dibagikan ke orang lain.")
    if not tk.env_status()["ready"]:
        print("PERHATIAN: .env belum lengkap — isi dulu di menu Pengaturan")
        print("  Kunci yang kosong:", ", ".join(tk.env_status()["missing_required"]))
    print("=" * 60)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=False, threaded=True)
