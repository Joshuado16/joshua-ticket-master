from __future__ import annotations

import os
import sqlite3
import uuid
import hmac
import secrets
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, make_response, redirect, render_template, request, session, url_for
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
csrf = CSRFProtect(app)


def connect_db():
    """Open a database connection for one request or setup operation."""
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email_address", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not username or not email or not password:
            flash("Enter a username, email address, and password.", "error")
        elif len(password) < 8:
            flash("Choose a password with at least 8 characters.", "error")
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
                flash("Your account is ready. Please sign in.", "success")
                return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with connect_db() as db:
            account = db.execute(
                "SELECT id, username, password_hash FROM accounts WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if account is None or not check_password_hash(account["password_hash"], password):
            flash("The username or password is incorrect.", "error")
        else:
            session.clear()
            session["account_id"] = account["id"]
            session["username"] = account["username"]
            return redirect(url_for("events"))
    return render_template("login.html")


@app.route("/signin", methods=["GET", "POST"])
def signin_legacy():
    return redirect(url_for("login"))


@app.route("/register")
def register_legacy():
    return redirect(url_for("signup"))


@app.route("/account")
def account():
    return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("home"))


@app.route("/events")
def events():
    with connect_db() as db:
        rows = db.execute(
            """SELECT events.*, accounts.username AS seller_name,
                      MAX(events.ticket_count - COALESCE((
                          SELECT SUM(quantity) FROM orders
                          WHERE orders.event_id = events.id AND
                                (orders.status = 'paid' OR
                                 (orders.status = 'pending' AND orders.created_at >= datetime('now', '-30 minutes')))
                      ), 0), 0) AS available_count
               FROM events JOIN accounts ON accounts.id = events.seller_id
               ORDER BY events.created_at DESC, events.id DESC"""
        ).fetchall()
    return render_template("events.html", events=rows)


@app.route("/seller")
def seller():
    if "account_id" not in session:
        flash("Sign in before listing an event.", "error")
        return redirect(url_for("login"))
    return render_template("seller.html")


@app.route("/newseller", methods=["POST"])
def newseller():
    if "account_id" not in session:
        flash("Sign in before listing an event.", "error")
        return redirect(url_for("login"))

    host = request.form.get("host", "").strip()
    venue = request.form.get("venue", "").strip()
    date_time = request.form.get("date_time", "").strip()
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
                image_data, image_type, price_cents, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session["account_id"], host, venue, date_time, ticket_count,
             description, phone, image_data, image_type, price_cents,
             CURRENCY),
        )
    flash("Your event has been listed.", "success")
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
                                (orders.status = 'paid' OR
                                 (orders.status = 'pending' AND orders.created_at >= datetime('now', '-30 minutes')))
                      ), 0) AS available_count
               FROM events WHERE id = ?""",
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
        "customizations": {"title": "Ticket Muster", "description": event["host"]},
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
            "SELECT image_data, image_type FROM events WHERE id = ?", (event_id,)
        ).fetchone()
    if event is None or event["image_data"] is None:
        abort(404)
    response = make_response(event["image_data"])
    response.headers["Content-Type"] = event["image_type"]
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


init_db()

if __name__ == "__main__":
    app.run(debug=False)





