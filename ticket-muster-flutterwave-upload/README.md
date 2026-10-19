# Ticket Muster

Ticket Muster is a learning prototype for listing local events and selling tickets. The uploaded repository was a partial group project. This repaired copy adds account management, event listings, and hosted payment checkout while preserving the original legacy SQLite tables. New app data uses `accounts`, `events`, and `orders`.

## Run locally

1. Use Python 3.10 or newer.
2. Create and activate a virtual environment.
3. Install packages with `python -m pip install -r requirements.txt`.
4. Copy `.env.example` to `.env`, set a random `SECRET_KEY`, and add your Flutterwave **test** secret key and dashboard webhook secret hash.
5. Start the app with `python project/app.py` and open `http://127.0.0.1:5000`.

Create an account, sign in, list an event with a ZMW ticket price, and choose **Continue to Flutterwave checkout**. The app sends buyers to Flutterwave Standard's hosted checkout. It verifies the transaction with Flutterwave's API before marking an order paid. A signed `verif-hash` webhook can confirm payment when the buyer does not return to the site. Unpaid checkout reservations stop counting toward availability after 30 minutes.

Configure the Flutterwave dashboard webhook URL as `https://your-domain/flutterwave-webhook`. For local testing, expose your app with a secure development tunnel and register its temporary HTTPS URL in the dashboard. Use test credentials and test payment methods while developing. Never put live keys in Git or browser code.

Flutterwave lists local Zambian mobile-money and card payment options, but live processing requires an approved merchant account and credentials. Flutterwave charges transaction fees; this code and its source archive are free. Ticket Muster is a prototype: it does not transfer seller payouts, issue scannable tickets, process refunds, or provide a complete dispute workflow. Do not use it for real sales before a security and operations review.

The SQLite database is intentionally excluded from the upload package. On first run the app creates the new tables in `project/ticket.db`; this avoids publishing the original repository's sample user records.
