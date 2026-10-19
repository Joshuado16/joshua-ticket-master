# TicketZMK

TicketZMK is a learning prototype for Zambian football, Gospel, and secular event listings. It was built by repairing and extending the uploaded Ticket Muster group project. New work is described in [CHANGES.md](CHANGES.md); the original project history and contributions are not reassigned.

## Run locally on Windows

From the extracted project folder in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe project\app.py
```

Open `http://127.0.0.1:5000`. Using the virtual-environment interpreter directly avoids PowerShell script-activation policy changes. The app creates `project/ticket.db` on first run and preserves the uploaded project's legacy tables.

Set a private random `SECRET_KEY` and Flutterwave test credentials in `.env` to test hosted checkout. Set `COOKIE_SECURE=true` only when serving over HTTPS. Never commit `.env` or live payment keys.

### Enable Google sign-in

Google sign-in uses OpenID Connect and only accepts Google profiles with a verified email. In Google Cloud Console, create an OAuth client of type **Web application**, add `http://127.0.0.1:5000` under authorized JavaScript origins and `http://127.0.0.1:5000/login/google/callback` under authorized redirect URIs. Put the resulting client ID and secret in the private `.env` file as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, then restart the app. For deployment, register the deployed HTTPS origin and callback too. Keep the secret private. Until configured, password sign-in works and the login page explains the setup requirement.

## Current features

- TicketZMK landing page inspired by the provided reference screenshots: deep teal and aqua palette, event hero, live counts, category cards, and mobile layout.
- Email or username sign-in, hashed passwords, CSRF-protected forms, password visibility controls, a 15-minute lock after repeated failed logins, secure cookie settings, and signed-in password changes.
- Optional Google OpenID Connect sign-in; Google-verified emails are used to create or match an account.
- Buyer accounts and free organizer profiles.
- Football, Gospel, and secular event listings with city, event details, poster, date, price, and availability.
- Event details, category/search filters, authenticated buyer checkout through Flutterwave Standard, and reserve-at-door bookings.
- Six clearly marked fictional demo listings across football, Gospel, and secular categories. Demo events cannot be booked or paid for.
- Buyer ticket history with booking references; organizer dashboard with event counts and confirmed online revenue; owner-only event editing/cancellation.
- Admin overview and event moderation. Create your account first, then promote it from the project folder with `python project/app.py promote-admin you@example.com` (use the virtual-environment Python path above in PowerShell).

## Integrations and prototype limits

Online checkout redirects to Flutterwave's hosted checkout. The app verifies transactions with Flutterwave before marking an order paid. Checkout will not start until valid test credentials are configured. A live account, public HTTPS webhook address, payout setup, and production deployment are not included.

This version uses one ticket price per event. It does not yet have multi-tier pricing, football seat maps, scannable QR codes, email confirmation for password-created accounts, OTP, self-service email password reset, organizer listing fees, refunds, or payout/dispute management. Google accounts do use Google's verified email claim. Social footer buttons currently link to each platform or share page; they are not official TicketZMK profiles. The app is not production-ready.

The reference Base44 app remains separate; these source changes apply to this Flask project. The upload ZIP excludes the local SQLite database, `.env`, and Python cache files.
