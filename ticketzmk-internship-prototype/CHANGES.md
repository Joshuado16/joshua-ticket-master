# Change notes

## Present in the uploaded group project

- The Flask/SQLite starting point and its original `users`, `registrants`, and `ticket_info` tables.
- The first set of HTML pages and image assets.
- The original app was incomplete: several flows did not connect from the form through the route into the database, and the payment and order system did not exist.

The repository does not establish which group member authored each original section, so these notes do not assign personal credit.

## Added in this learning copy

- A consistent account sign-up and sign-in flow using password hashes.
- Event listings with price, ticket capacity, and optional image.
- Flutterwave Standard hosted checkout using ZMW, with payment verification before an order is marked paid.
- A signed Flutterwave webhook, CSRF protection on browser forms, and a 30-minute inventory hold while checkout is open.
- A refreshed responsive Ticket Muster visual theme, event search and filters, and a clearer Flutterwave checkout label.
- TicketZMK teal/aqua home and event pages based on the supplied screenshots, responsive event cards, category filters, and event detail pages.
- Buyer ticket history, booking references, optional door-payment reservations, free organizer profiles, an owner-only event dashboard, event editing/cancellation, and an admin moderation view.
- Email-or-username sign-in, stronger minimum password length for new accounts, hashed passwords, short lockouts after repeated bad logins, secure session-cookie settings, and password changes.
- Optional Google OpenID Connect sign-in using Google's verified email, requiring the owner's OAuth client credentials.
- Six clearly labeled fictional preview events in all three categories, with existing project photos; preview-only bookings and payments are disabled.
- Facebook, TikTok, and WhatsApp footer links, including a WhatsApp event-sharing link.
- Setup files and instructions. The database and live secrets are excluded from the upload archive.

This integration is new work for the repaired learning copy. It has not been tested against a live or sandbox Flutterwave account because no merchant credentials were provided. Email confirmation, OTP, password reset delivery, Google sign-in, ticket tiers, seat maps, QR tickets, and automated email remain future work.
