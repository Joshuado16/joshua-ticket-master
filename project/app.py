from __future__ import annotations

import os
import sys
import sqlite3
import uuid
import hmac
import hashlib
import secrets
from contextlib import contextmanager
from datetime import date, timedelta
from urllib.parse import urlparse
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
try:
    from authlib.integrations.flask_client import OAuth
except ImportError:  # Password sign-in remains available if optional OAuth isn't installed.
    OAuth = None
from dotenv import load_dotenv
from flask import Flask, abort, flash, make_response, redirect, render_template, request, send_from_directory, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
DATABASE = BASE_DIR / "ticket.db"
MAX_IMAGE_BYTES = 3 * 1024 * 1024
CURRENCY = "ZMW"
FLUTTERWAVE_API = "https://api.flutterwave.com/v3"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES + 64 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
csrf = CSRFProtect(app)
oauth = OAuth(app) if OAuth else None
google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_LOGIN_ENABLED = bool(
    oauth and google_client_id and google_client_secret
    and not google_client_id.startswith("add-your-")
    and not google_client_secret.startswith("add-your-")
)
if GOOGLE_LOGIN_ENABLED:
    oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


@app.context_processor
def template_globals():
    return {"now_year": date.today().year, "google_login_enabled": GOOGLE_LOGIN_ENABLED}


@contextmanager
def connect_db():
    """Open a database connection for one request or setup operation."""
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    """Create the improved app tables without deleting the original project tables."""
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'buyer',
                google_sub TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS organizer_profiles (
                account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                org_name TEXT NOT NULL,
                bio TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS auth_attempts (
                login_key TEXT PRIMARY KEY,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL REFERENCES accounts(id),
                host TEXT NOT NULL,
                venue TEXT NOT NULL,
                date_time TEXT NOT NULL,
                ticket_count INTEGER NOT NULL CHECK (ticket_count > 0),
                ticketinfo TEXT NOT NULL,
                phone TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'secular',
                city TEXT NOT NULL DEFAULT 'Lusaka',
                status TEXT NOT NULL DEFAULT 'published',
                featured INTEGER NOT NULL DEFAULT 0,
                demo_image TEXT,
                image_data BLOB,
                image_type TEXT,
                price_cents INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'ZMW',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id),
                buyer_id INTEGER NOT NULL REFERENCES accounts(id),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                amount_total INTEGER NOT NULL,
                payment_method TEXT NOT NULL DEFAULT 'online',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        event_columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
        if "price_cents" not in event_columns:
            db.execute("ALTER TABLE events ADD COLUMN price_cents INTEGER NOT NULL DEFAULT 0")
        if "currency" not in event_columns:
            db.execute("ALTER TABLE events ADD COLUMN currency TEXT NOT NULL DEFAULT 'ZMW'")
        for column, definition in {
            "event_type": "TEXT NOT NULL DEFAULT 'secular'",
            "city": "TEXT NOT NULL DEFAULT 'Lusaka'",
            "status": "TEXT NOT NULL DEFAULT 'published'",
            "featured": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column not in event_columns:
                db.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")
        account_columns = {row[1] for row in db.execute("PRAGMA table_info(accounts)")}
        if "role" not in account_columns:
            db.execute("ALTER TABLE accounts ADD COLUMN role TEXT NOT NULL DEFAULT 'buyer'")
        if "google_sub" not in account_columns:
            db.execute("ALTER TABLE accounts ADD COLUMN google_sub TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_google_sub ON accounts(google_sub) WHERE google_sub IS NOT NULL")
        if "demo_image" not in event_columns:
            db.execute("ALTER TABLE events ADD COLUMN demo_image TEXT")
        order_columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
        if "payment_method" not in order_columns:
            db.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'online'")
        seed_demo_events(db)


def seed_demo_events(db):
    """Add clearly labeled, fictional starter listings once for a fresh database."""
    email = "ticketzmk-demo@ticketzmk.local"
    organizer = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()
    if organizer is None:
        cursor = db.execute(
            "INSERT INTO accounts (username, email, password_hash, role) VALUES (?, ?, ?, 'organizer')",
            ("TicketZMK Demo", email, generate_password_hash(secrets.token_urlsafe(32))),
        )
        organizer_id = cursor.lastrowid
        db.execute(
            "INSERT INTO organizer_profiles (account_id, org_name, bio, phone, verified) VALUES (?, ?, ?, ?, 1)",
            (organizer_id, "TicketZMK Demo Listings", "Fictional starter listings for previewing the prototype. Not real events.", "0000000000"),
        )
    else:
        organizer_id = organizer["id"]
    if db.execute("SELECT 1 FROM events WHERE seller_id = ? LIMIT 1", (organizer_id,)).fetchone():
        return
    today = date.today()
    examples = [
        ("Lusaka City vs Copperbelt United", "football", "National Heroes Stadium", "Lusaka", 21, 15, "homepage.jpg", "Demo match listing. Fictional teams and event details for testing the event catalogue."),
        ("Copperbelt Community Derby", "football", "Levy Mwanawasa Stadium", "Ndola", 42, 20, "football.jpg", "Demo football listing for exploring categories, dates and ticket pricing."),
        ("Songs of Hope Gospel Night", "gospel", "Mulungushi Conference Centre", "Lusaka", 35, 10, "homepage.jpg", "Demo Gospel concert listing. Please treat all seeded details as sample content."),
        ("Praise & Worship Live", "gospel", "Kitwe Little Theatre", "Kitwe", 55, 12, "basketball.jpg", "A fictional starter listing to demonstrate Gospel category browsing."),
        ("Lusaka Summer Sounds", "secular", "Showgrounds Main Arena", "Lusaka", 70, 30, "basketball.jpg", "Demo secular music event for preview purposes; not a real booking."),
        ("Copperbelt Live Sessions", "secular", "Garden Court Arena", "Kitwe", 90, 18, "homepage.jpg", "Fictional event details demonstrate city search, event cards and listing images."),
    ]
    for title, category, venue, city, days, price, image_name, description in examples:
        image_path = BASE_DIR / "templates" / image_name
        db.execute(
            """INSERT INTO events (seller_id, host, venue, date_time, ticket_count, ticketinfo, phone,
               event_type, city, status, featured, demo_image, price_cents, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', 0, ?, ?, 'ZMW')""",
            (organizer_id, title, venue, (today + timedelta(days=days)).strftime("%Y-%m-%d %H:%M"), 120,
             description, "0000000000", category, city, image_name,
             price * 100),
        )


def fetch_events(db, event_id=None):
    """Return public events with seller names and inventory after active holds."""
    sql = """SELECT events.*, accounts.username AS seller_name,
                     COALESCE(organizer_profiles.org_name, accounts.username) AS organizer_name,
                     MAX(events.ticket_count - COALESCE((
                         SELECT SUM(quantity) FROM orders
                         WHERE orders.event_id = events.id AND
                               (orders.status IN ('paid', 'reserved') OR
                                (orders.status = 'pending' AND orders.created_at >= datetime('now', '-30 minutes')))
                     ), 0), 0) AS available_count
              FROM events JOIN accounts ON accounts.id = events.seller_id
              LEFT JOIN organizer_profiles ON organizer_profiles.account_id = accounts.id
              WHERE events.status = 'published'"""
    args = []
    if event_id is not None:
        sql += " AND events.id = ?"
        args.append(event_id)
    sql += " ORDER BY events.featured DESC, events.date_time ASC, events.id DESC"
    return db.execute(sql, args).fetchall()


@app.route("/")
def home():
    with connect_db() as db:
        events = fetch_events(db)
    stats = {
        "events": len(events),
        "football": sum(event["event_type"] == "football" for event in events),
        "music": sum(event["event_type"] in {"gospel", "secular"} for event in events),
    }
    return render_template("index.html", events=events[:4], stats=stats)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email_address", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not username or not email or not password:
            flash("Enter a username, email address, and password.", "error")
        elif len(password) < 10:
            flash("Choose a password with at least 10 characters.", "error")
        elif password != confirm_password:
            flash("The passwords do not match.", "error")
        else:
            try:
                with connect_db() as db:
                    db.execute(
                        "INSERT INTO accounts (username, email, password_hash) VALUES (?, ?, ?)",
                        (username, email, generate_password_hash(password)),
                    )
            except sqlite3.IntegrityError:
                flash("That username or email is already registered.", "error")
            else:
                with connect_db() as db:
                    account = db.execute("SELECT id, username, role FROM accounts WHERE email = ?", (email,)).fetchone()
                session.clear()
                session["account_id"] = account["id"]
                session["username"] = account["username"]
                session["role"] = account["role"]
                session.permanent = True
                flash("Welcome to TicketZMK. Your account is ready.", "success")
                return redirect(url_for("events"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        login_key = hashlib.sha256(f"{request.remote_addr or 'local'}:{username.lower()}".encode()).hexdigest()
        with connect_db() as db:
            attempt = db.execute(
                "SELECT failed_attempts, locked_until, COALESCE(locked_until > datetime('now'), 0) AS is_locked FROM auth_attempts WHERE login_key = ?",
                (login_key,),
            ).fetchone()
            if attempt and attempt["is_locked"]:
                flash("We couldn't sign you in. Wait 15 minutes and try again.", "error")
                return render_template("login.html")
            account = db.execute(
                "SELECT id, username, password_hash, role FROM accounts WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE",
                (username, username),
            ).fetchone()
        if account is None or not check_password_hash(account["password_hash"], password):
            failures = (attempt["failed_attempts"] + 1) if attempt and not attempt["locked_until"] else 1
            with connect_db() as db:
                db.execute(
                    """INSERT INTO auth_attempts (login_key, failed_attempts, locked_until)
                       VALUES (?, ?, CASE WHEN ? >= 5 THEN datetime('now', '+15 minutes') ELSE NULL END)
                       ON CONFLICT(login_key) DO UPDATE SET failed_attempts = excluded.failed_attempts,
                       locked_until = excluded.locked_until""",
                    (login_key, failures, failures),
                )
            flash("The email/username or password is incorrect.", "error")
        else:
            with connect_db() as db:
                db.execute("DELETE FROM auth_attempts WHERE login_key = ?", (login_key,))
            session.clear()
            session["account_id"] = account["id"]
            session["username"] = account["username"]
            session["role"] = account["role"]
            session.permanent = True
            next_url = safe_next_url(request.form.get("next", ""))
            if next_url:
                return redirect(next_url)
            return redirect(url_for("organizer") if account["role"] == "organizer" else url_for("events"))
    return render_template("login.html")


@app.route("/login/google")
def login_google():
    if not GOOGLE_LOGIN_ENABLED:
        flash("Google sign-in is not configured yet. You can still sign in with your email and password.", "error")
        return redirect(url_for("login"))
    session["google_next"] = safe_next_url(request.args.get("next", ""))
    return oauth.google.authorize_redirect(url_for("google_callback", _external=True))


@app.route("/login/google/callback")
def google_callback():
    if not GOOGLE_LOGIN_ENABLED:
        return redirect(url_for("login"))
    try:
        token = oauth.google.authorize_access_token()
        profile = token.get("userinfo") or {}
    except Exception:
        app.logger.exception("Google sign-in could not be completed")
        flash("Google sign-in could not be completed. Please try again or use your password.", "error")
        return redirect(url_for("login"))
    email = str(profile.get("email", "")).strip().lower()
    subject = str(profile.get("sub", "")).strip()
    verified = profile.get("email_verified") is True or str(profile.get("email_verified", "")).lower() == "true"
    if not email or not subject or not verified:
        flash("Google did not provide a verified email address. Use password sign-in instead.", "error")
        return redirect(url_for("login"))
    with connect_db() as db:
        account = db.execute("SELECT id, username, role FROM accounts WHERE google_sub = ?", (subject,)).fetchone()
        if account is None:
            account = db.execute("SELECT id, username, role FROM accounts WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
            if account:
                db.execute("UPDATE accounts SET google_sub = ? WHERE id = ? AND google_sub IS NULL", (subject, account["id"]))
            else:
                base_name = (profile.get("name") or email.split("@", 1)[0]).strip()[:32] or "TicketZMK member"
                username = base_name
                suffix = 1
                while db.execute("SELECT 1 FROM accounts WHERE username = ? COLLATE NOCASE", (username,)).fetchone():
                    suffix += 1
                    username = f"{base_name[:32-len(str(suffix))-1]}{suffix}"
                cursor = db.execute(
                    "INSERT INTO accounts (username, email, password_hash, role, google_sub) VALUES (?, ?, ?, 'buyer', ?)",
                    (username, email, generate_password_hash(secrets.token_urlsafe(32)), subject),
                )
                account = {"id": cursor.lastrowid, "username": username, "role": "buyer"}
    session.clear()
    session["account_id"] = account["id"]
    session["username"] = account["username"]
    session["role"] = account["role"]
    session.permanent = True
    return redirect(session.pop("google_next", "") or url_for("events"))


def safe_next_url(value):
    value = (value or "").strip()
    parsed = urlparse(value)
    if value.startswith("/") and not parsed.netloc and not value.startswith("//"):
        return value
    return ""


@app.route("/signin", methods=["GET", "POST"])
def signin_legacy():
    return redirect(url_for("login"))


@app.route("/register")
def register_legacy():
    return redirect(url_for("signup"))


@app.route("/account")
def account():
    if "account_id" not in session:
        return redirect(url_for("login"))
    with connect_db() as db:
        account = db.execute("SELECT username, email, role FROM accounts WHERE id = ?", (session["account_id"],)).fetchone()
    return render_template("account.html", account=account)


@app.route("/account/password", methods=["POST"])
def change_password():
    if "account_id" not in session:
        return redirect(url_for("login"))
    current = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    with connect_db() as db:
        account = db.execute("SELECT password_hash FROM accounts WHERE id = ?", (session["account_id"],)).fetchone()
        if account is None or not check_password_hash(account["password_hash"], current):
            flash("Your current password did not match.", "error")
        elif len(new_password) < 10:
            flash("Choose a new password with at least 10 characters.", "error")
        elif new_password != confirm:
            flash("The new passwords do not match.", "error")
        else:
            db.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), session["account_id"]))
            flash("Your password has been changed.", "success")
    return redirect(url_for("account"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("home"))


@app.route("/events")
def events():
    with connect_db() as db:
        rows = fetch_events(db)
    return render_template("events.html", events=rows)


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    with connect_db() as db:
        event = next(iter(fetch_events(db, event_id)), None)
    if event is None:
        abort(404)
    return render_template("event_detail.html", event=event)


@app.route("/my-tickets")
def my_tickets():
    if "account_id" not in session:
        flash("Sign in to view your tickets.", "error")
        return redirect(url_for("login", next=url_for("my_tickets")))
    with connect_db() as db:
        tickets = db.execute(
            """SELECT orders.*, events.host, events.venue, events.city, events.date_time, events.event_type
               FROM orders JOIN events ON events.id = orders.event_id
               WHERE orders.buyer_id = ? ORDER BY orders.created_at DESC""",
            (session["account_id"],),
        ).fetchall()
    return render_template("my_tickets.html", tickets=tickets)


@app.route("/seller")
def seller():
    if "account_id" not in session:
        flash("Sign in before listing an event.", "error")
        return redirect(url_for("login"))
    with connect_db() as db:
        profile = db.execute("SELECT 1 FROM organizer_profiles WHERE account_id = ?", (session["account_id"],)).fetchone()
    if profile is None:
        flash("Create your free organizer profile before listing an event.", "error")
        return redirect(url_for("organizer_register"))
    return render_template("seller.html")


@app.route("/organizer/register", methods=["GET", "POST"])
def organizer_register():
    if "account_id" not in session:
        flash("Sign in or create an account to become an organizer.", "error")
        return redirect(url_for("signup"))
    if request.method == "POST":
        org_name = request.form.get("org_name", "").strip()
        bio = request.form.get("bio", "").strip()
        phone = request.form.get("phone", "").strip()
        website = request.form.get("website", "").strip()
        if not org_name or not phone:
            flash("Add an organizer name and contact phone.", "error")
        else:
            with connect_db() as db:
                db.execute(
                    """INSERT INTO organizer_profiles (account_id, org_name, bio, phone, website)
                       VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET
                       org_name=excluded.org_name, bio=excluded.bio, phone=excluded.phone, website=excluded.website""",
                    (session["account_id"], org_name, bio, phone, website),
                )
                db.execute("UPDATE accounts SET role = 'organizer' WHERE id = ? AND role != 'admin'", (session["account_id"],))
            with connect_db() as db:
                role = db.execute("SELECT role FROM accounts WHERE id = ?", (session["account_id"],)).fetchone()["role"]
            session["role"] = role
            flash("Organizer profile saved. You can now list events.", "success")
            return redirect(url_for("organizer"))
    return render_template("organizer_register.html")


@app.route("/organizer")
def organizer():
    if "account_id" not in session:
        flash("Sign in to open your organizer dashboard.", "error")
        return redirect(url_for("login"))
    with connect_db() as db:
        profile = db.execute("SELECT * FROM organizer_profiles WHERE account_id = ?", (session["account_id"],)).fetchone()
        if profile is None:
            flash("Set up your free organizer profile first.", "error")
            return redirect(url_for("organizer_register"))
        events = db.execute(
            """SELECT events.*,
                      (SELECT COUNT(*) FROM orders WHERE orders.event_id = events.id) AS booking_count,
                      (SELECT COALESCE(SUM(amount_total), 0) FROM orders WHERE orders.event_id = events.id AND orders.status = 'paid') AS revenue_cents
               FROM events WHERE seller_id = ? ORDER BY created_at DESC""",
            (session["account_id"],),
        ).fetchall()
    return render_template("organizer.html", profile=profile, events=events)


@app.route("/newseller", methods=["POST"])
def newseller():
    if "account_id" not in session:
        flash("Sign in before listing an event.", "error")
        return redirect(url_for("login"))
    with connect_db() as db:
        profile = db.execute("SELECT 1 FROM organizer_profiles WHERE account_id = ?", (session["account_id"],)).fetchone()
    if profile is None:
        flash("Create your free organizer profile before listing an event.", "error")
        return redirect(url_for("organizer_register"))

    host = request.form.get("host", "").strip()
    venue = request.form.get("venue", "").strip()
    date_time = request.form.get("date_time", "").strip()
    event_type = request.form.get("event_type", "secular").strip().lower()
    city = request.form.get("city", "Lusaka").strip() or "Lusaka"
    ticket_count_text = request.form.get("ticket_count", "").strip()
    price_text = request.form.get("price", "").strip()
    description = request.form.get("description", "").strip()
    phone = request.form.get("phone", "").strip()
    image = request.files.get("image")

    if not all((host, venue, date_time, ticket_count_text, price_text, description, phone)):
        flash("Fill in all required event fields.", "error")
        return redirect(url_for("seller"))
    try:
        ticket_count = int(ticket_count_text)
        price = Decimal(price_text)
        if not price.is_finite() or price <= 0 or price != price.quantize(Decimal("0.01")):
            raise InvalidOperation
        price_cents = int(price * 100)
    except (ValueError, InvalidOperation, OverflowError):
        flash("Ticket count and price must be valid numbers.", "error")
        return redirect(url_for("seller"))
    if ticket_count <= 0 or price_cents < 1:
        flash("Enter at least one ticket and a positive ZMW price.", "error")
        return redirect(url_for("seller"))
    if event_type not in {"football", "gospel", "secular"}:
        flash("Choose Football, Gospel, or Secular for the event category.", "error")
        return redirect(url_for("seller"))
    image_data = None
    image_type = None
    if image and image.filename:
        image_data = image.read(MAX_IMAGE_BYTES + 1)
        if len(image_data) > MAX_IMAGE_BYTES:
            flash("The image must be 3 MB or smaller.", "error")
            return redirect(url_for("seller"))
        if image.mimetype not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            flash("Upload a JPEG, PNG, GIF, or WebP image.", "error")
            return redirect(url_for("seller"))
        image_type = image.mimetype

    with connect_db() as db:
        db.execute(
            """INSERT INTO events
               (seller_id, host, venue, date_time, ticket_count, ticketinfo, phone,
                event_type, city, image_data, image_type, price_cents, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session["account_id"], host, venue, date_time, ticket_count,
             description, phone, event_type, city, image_data, image_type, price_cents,
             CURRENCY),
        )
    flash("Your event has been submitted and is now visible.", "success")
    return redirect(url_for("events"))


def verify_flutterwave_transaction(order_id, transaction_id):
    """Verify the transaction with Flutterwave before marking the order paid."""
    secret_key = os.environ.get("FLW_SECRET_KEY", "")
    transaction_id = str(transaction_id or "")
    if not secret_key or not transaction_id.isdigit():
        return False
    response = requests.get(
        f"{FLUTTERWAVE_API}/transactions/{transaction_id}/verify",
        headers={"Authorization": f"Bearer {secret_key}"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    transaction = payload.get("data", {})
    with connect_db() as db:
        order = db.execute(
            "SELECT orders.amount_total, events.currency FROM orders JOIN events ON events.id = orders.event_id WHERE orders.id = ?",
            (order_id,),
        ).fetchone()
    if order is None:
        return False
    try:
        received_amount = Decimal(str(transaction.get("amount", "0")))
        expected_amount = Decimal(order["amount_total"]) / Decimal(100)
    except InvalidOperation:
        return False
    if not (
        payload.get("status") == "success"
        and transaction.get("status") == "successful"
        and transaction.get("tx_ref") == order_id
        and transaction.get("currency") == order["currency"]
        and received_amount >= expected_amount
    ):
        return False
    with connect_db() as db:
        db.execute("UPDATE orders SET status = 'paid' WHERE id = ? AND status = 'pending'", (order_id,))
    return True


@app.route("/checkout/<int:event_id>", methods=["POST"])
def create_checkout(event_id):
    if "account_id" not in session:
        flash("Sign in before buying tickets.", "error")
        return redirect(url_for("login"))
    secret_key = os.environ.get("FLW_SECRET_KEY", "")
    if not secret_key:
        flash("Payments are not configured yet. Add your Flutterwave secret key to enable checkout.", "error")
        return redirect(url_for("events"))
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        flash("Choose a valid ticket quantity.", "error")
        return redirect(url_for("events"))
    if quantity < 1:
        flash("Choose at least one ticket.", "error")
        return redirect(url_for("events"))

    order_id = str(uuid.uuid4())
    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        event = db.execute(
            """SELECT events.*,
                      events.ticket_count - COALESCE((
                          SELECT SUM(quantity) FROM orders
                          WHERE orders.event_id = events.id AND
                                (orders.status IN ('paid', 'reserved') OR
                                 (orders.status = 'pending' AND orders.created_at >= datetime('now', '-30 minutes')))
                      ), 0) AS available_count
               FROM events WHERE id = ? AND status = 'published'""",
            (event_id,),
        ).fetchone()
        if event is None or event["price_cents"] < 1:
            flash("This event is not available for online checkout.", "error")
            return redirect(url_for("events"))
        if event["available_count"] < quantity:
            flash("There are not enough tickets remaining for that quantity.", "error")
            return redirect(url_for("events"))
        db.execute(
            "INSERT INTO orders (id, event_id, buyer_id, quantity, amount_total) VALUES (?, ?, ?, ?, ?)",
            (order_id, event_id, session["account_id"], quantity, event["price_cents"] * quantity),
        )
        account = db.execute("SELECT username, email FROM accounts WHERE id = ?", (session["account_id"],)).fetchone()

    payment_payload = {
        "tx_ref": order_id,
        "amount": f"{Decimal(event['price_cents'] * quantity) / Decimal(100):.2f}",
        "currency": event["currency"],
        "redirect_url": url_for("payment_result", _external=True),
        "customer": {"email": account["email"], "name": account["username"]},
        "customizations": {"title": "TicketZMK", "description": event["host"]},
        "meta": {"order_id": order_id, "event_id": event_id},
        "configurations": {"session_duration": 30, "max_retry_attempt": 3},
    }
    try:
        response = requests.post(
            f"{FLUTTERWAVE_API}/payments",
            headers={"Authorization": f"Bearer {secret_key}"},
            json=payment_payload,
            timeout=20,
        )
        response.raise_for_status()
        checkout_data = response.json()
        checkout_url = checkout_data.get("data", {}).get("link")
        if checkout_data.get("status") != "success" or not checkout_url:
            raise requests.RequestException("Flutterwave did not return a checkout URL")
    except (requests.RequestException, ValueError):
        with connect_db() as db:
            db.execute("UPDATE orders SET status = 'failed' WHERE id = ?", (order_id,))
        flash("Flutterwave could not start checkout. Please try again.", "error")
        return redirect(url_for("events"))
    return redirect(checkout_url, code=303)


@app.route("/reserve/<int:event_id>", methods=["POST"])
def reserve_at_door(event_id):
    if "account_id" not in session:
        flash("Sign in to reserve tickets.", "error")
        return redirect(url_for("login", next=url_for("event_detail", event_id=event_id)))
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        flash("Choose a valid ticket quantity.", "error")
        return redirect(url_for("event_detail", event_id=event_id))
    if quantity < 1:
        flash("Choose at least one ticket.", "error")
        return redirect(url_for("event_detail", event_id=event_id))
    order_id = str(uuid.uuid4())
    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        event = db.execute(
            """SELECT events.*,
                      events.ticket_count - COALESCE((SELECT SUM(quantity) FROM orders
                      WHERE orders.event_id = events.id AND
                      (orders.status IN ('paid', 'reserved') OR
                       (orders.status = 'pending' AND orders.created_at >= datetime('now', '-30 minutes')))), 0) AS available_count
               FROM events WHERE events.id = ? AND events.status = 'published'""",
            (event_id,),
        ).fetchone()
        if event is None or event["available_count"] < quantity:
            flash("That event does not have enough tickets available.", "error")
            return redirect(url_for("event_detail", event_id=event_id))
        db.execute(
            "INSERT INTO orders (id, event_id, buyer_id, quantity, amount_total, payment_method, status) VALUES (?, ?, ?, ?, ?, 'door', 'reserved')",
            (order_id, event_id, session["account_id"], quantity, event["price_cents"] * quantity),
        )
    flash("Your door-payment reservation is saved. Find its reference in My Tickets.", "success")
    return redirect(url_for("my_tickets"))


@app.route("/organizer/events/<int:event_id>", methods=["GET", "POST"])
def manage_event(event_id):
    if "account_id" not in session:
        return redirect(url_for("login"))
    with connect_db() as db:
        event = db.execute("SELECT * FROM events WHERE id = ? AND seller_id = ?", (event_id, session["account_id"])).fetchone()
        if event is None:
            abort(404)
        if request.method == "POST":
            title = request.form.get("host", "").strip()
            venue = request.form.get("venue", "").strip()
            date_time = request.form.get("date_time", "").strip()
            description = request.form.get("description", "").strip()
            event_type = request.form.get("event_type", "").strip().lower()
            city = request.form.get("city", "").strip()
            try:
                ticket_count = int(request.form.get("ticket_count", ""))
                price = Decimal(request.form.get("price", ""))
                if ticket_count < 1 or not price.is_finite() or price <= 0 or price != price.quantize(Decimal("0.01")):
                    raise InvalidOperation
                price_cents = int(price * 100)
            except (ValueError, InvalidOperation, OverflowError):
                ticket_count = 0
                price_cents = 0
            if not all((title, venue, date_time, description)):
                flash("Title, venue, date, and event details are required.", "error")
            elif event_type not in {"football", "gospel", "secular"} or not city or ticket_count < 1 or price_cents < 1:
                flash("Check the category, city, ticket quantity, and ZMW price.", "error")
            else:
                sold_or_reserved = db.execute(
                    """SELECT COALESCE(SUM(quantity), 0) FROM orders WHERE event_id = ? AND
                       (status IN ('paid', 'reserved') OR (status = 'pending' AND created_at >= datetime('now', '-30 minutes')))""",
                    (event_id,),
                ).fetchone()[0]
                if ticket_count < sold_or_reserved:
                    flash(f"Ticket quantity cannot be below the {sold_or_reserved} sold or reserved tickets.", "error")
                else:
                    db.execute(
                        "UPDATE events SET host = ?, venue = ?, date_time = ?, ticketinfo = ?, event_type = ?, city = ?, ticket_count = ?, price_cents = ? WHERE id = ? AND seller_id = ?",
                        (title, venue, date_time, description, event_type, city, ticket_count, price_cents, event_id, session["account_id"]),
                    )
                    flash("Event details updated.", "success")
                    return redirect(url_for("organizer"))
    return render_template("manage_event.html", event=event)


@app.route("/organizer/events/<int:event_id>/cancel", methods=["POST"])
def cancel_event(event_id):
    if "account_id" not in session:
        return redirect(url_for("login"))
    with connect_db() as db:
        result = db.execute("UPDATE events SET status = 'cancelled' WHERE id = ? AND seller_id = ?", (event_id, session["account_id"]))
        if result.rowcount != 1:
            abort(404)
    flash("Event cancelled. It is no longer listed for buyers.", "success")
    return redirect(url_for("organizer"))


@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        abort(403)
    with connect_db() as db:
        events = db.execute("SELECT events.*, accounts.username FROM events JOIN accounts ON accounts.id = events.seller_id ORDER BY created_at DESC").fetchall()
        metrics = db.execute("SELECT COUNT(*) AS bookings, COALESCE(SUM(CASE WHEN status = 'paid' THEN amount_total ELSE 0 END), 0) AS revenue FROM orders").fetchone()
    return render_template("admin.html", events=events, metrics=metrics)


@app.route("/admin/events/<int:event_id>/status", methods=["POST"])
def admin_event_status(event_id):
    if session.get("role") != "admin":
        abort(403)
    status = request.form.get("status", "")
    if status not in {"published", "suspended", "cancelled"}:
        abort(400)
    with connect_db() as db:
        result = db.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
        if result.rowcount != 1:
            abort(404)
    flash("Event status updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/payment-result")
def payment_result():
    if "account_id" not in session:
        return redirect(url_for("login"))
    order_id = request.args.get("tx_ref", "")
    transaction_id = request.args.get("transaction_id", "")
    provider_status = request.args.get("status", "")
    with connect_db() as db:
        order = db.execute(
            """SELECT orders.*, events.host FROM orders JOIN events ON events.id = orders.event_id
               WHERE orders.id = ? AND buyer_id = ?""",
            (order_id, session["account_id"]),
        ).fetchone()
    if order is None:
        abort(404)
    if order["status"] == "pending" and provider_status == "successful" and transaction_id:
        try:
            verify_flutterwave_transaction(order_id, transaction_id)
        except requests.RequestException:
            app.logger.exception("Could not verify Flutterwave transaction %s", transaction_id)
    with connect_db() as db:
        order = db.execute(
            """SELECT orders.*, events.host FROM orders JOIN events ON events.id = orders.event_id
               WHERE orders.id = ? AND buyer_id = ?""",
            (order_id, session["account_id"]),
        ).fetchone()
    return render_template("payment_result.html", order=order)


@app.route("/flutterwave-webhook", methods=["POST"])
@csrf.exempt
def flutterwave_webhook():
    secret_hash = os.environ.get("FLW_SECRET_HASH", "")
    signature = request.headers.get("verif-hash", "")
    if not secret_hash or not hmac.compare_digest(signature, secret_hash):
        return "Invalid webhook signature", 401
    payload = request.get_json(silent=True) or {}
    transaction = payload.get("data", {})
    order_id = transaction.get("tx_ref")
    transaction_id = transaction.get("id")
    if transaction.get("status") == "successful" and order_id and transaction_id:
        try:
            verify_flutterwave_transaction(order_id, transaction_id)
        except requests.RequestException:
            app.logger.exception("Could not verify Flutterwave webhook transaction %s", transaction_id)
            return "Verification temporarily unavailable", 503
    return "", 200


@app.route("/event-image/<int:event_id>")
def event_image(event_id):
    from flask import abort, make_response

    with connect_db() as db:
        event = db.execute(
            "SELECT image_data, image_type, demo_image FROM events WHERE id = ?", (event_id,)
        ).fetchone()
    if event is None:
        abort(404)
    if event["demo_image"] in {"homepage.jpg", "football.jpg", "basketball.jpg"}:
        return send_from_directory(BASE_DIR / "templates", event["demo_image"])
    if event["image_data"] is None:
        abort(404)
    response = make_response(event["image_data"])
    response.headers["Content-Type"] = event["image_type"]
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/assets/<path:filename>")
def project_asset(filename):
    if filename not in {"homepage.jpg", "football.jpg", "basketball.jpg"}:
        abort(404)
    return send_from_directory(BASE_DIR / "templates", filename)


init_db()

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "promote-admin":
        with connect_db() as db:
            result = db.execute("UPDATE accounts SET role = 'admin' WHERE email = ? COLLATE NOCASE", (sys.argv[2].strip(),))
        if result.rowcount != 1:
            raise SystemExit("No account matched that email. Create an account first.")
        print("Account promoted to admin.")
    else:
        app.run(debug=False)





