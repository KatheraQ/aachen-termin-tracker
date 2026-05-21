# Aachen Termin Tracker

**🌐 Live at [aachen-termin.online](https://aachen-termin.online)**

Watches `https://termine.staedteregion-aachen.de/auslaenderamt/` and emails subscribers
when new appointment slots appear (visa extension / 延签, first application, eAT pickup).

Built for fellow international students at RWTH Aachen who keep missing the Termin window.

> 🇨🇳 给亚琛 RWTH 留学生用的外管局 Termin 抢号提醒。订阅邮箱后,只要有新空位放出,
> 你的邮箱就会收到通知。完全免费,代码开源。

## Architecture

- **`app/server.py`** — FastAPI signup / verify / unsubscribe (with GDPR-required Impressum + Datenschutz).
- **`app/scraper.py`** — Playwright driver for the 6-step booking flow (we only need the first 3 to read availability).
- **`run_scraper.py`** — Background loop: polls every 60–120s, diffs against `seen_slots`, emails subscribers.
- **SQLite** at `data/tracker.db` — users, subscriptions, seen slots, sent notifications.
- **Gmail SMTP** via `aiosmtplib` for outbound mail.

## Setup

```bash
cd ~/aachen-termin-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Edit `config.yaml`:
- Set `app.secret_key` to a random string (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`).
- Set `app.base_url` to whatever URL the server is reachable at.
- Fill in your `smtp.username`, `smtp.password` (Gmail **App Password**, see below), and `smtp.from_address`.
- Fill in `legal.impressum.*` with your real name and address (German law — Impressumspflicht).

### Gmail App Password — how to get one

App passwords are 16-character one-off passwords for SMTP/IMAP that bypass 2FA. Your regular Gmail login won't work for SMTP.

1. Turn on 2-factor auth on your Google Account if it isn't on already: <https://myaccount.google.com/security> → "2-Step Verification".
2. Once 2FA is on, go to <https://myaccount.google.com/apppasswords>.
3. Pick "Mail" as the app, "Other" as the device, name it "Aachen Termin Tracker", click Generate.
4. Copy the 16-character password (spaces don't matter) into `config.yaml` under `smtp.password`.

⚠️ Don't commit `config.yaml` to a public repo with the password filled in. Add it to `.gitignore` or use `config.local.yaml`.

## First run — discover real selectors

The selectors in `app/scraper.py` are placeholders. Lock them in before running the loop:

```bash
python discover.py
```

This opens a headed Chromium window and dumps the DOM at the landing page + after you manually click through to the calendar. Use the JSON output to update:
- `SEL_BEHOERDE_SELECT` (the dropdown on step 1)
- `SEL_ANLIEGEN_INPUT` + `SEL_ANLIEGEN_LABEL_ROW` (the quantity inputs on step 2)
- `SEL_CALENDAR_CELL_AVAILABLE` (clickable free dates on step 3)
- `SEL_NO_SLOTS_BANNER` (the "keine Termine frei" text)
- The Anliegen `label_de` strings in `config.yaml` (must match the page exactly)
- `scraper.behoerde_label` in `config.yaml`

## Running

Two processes (use `tmux` / `screen` / systemd / a Procfile):

```bash
python run_server.py   # FastAPI on http://127.0.0.1:8765
python run_scraper.py  # background polling loop
```

Visit <http://127.0.0.1:8765> to subscribe. Subscriptions require email confirmation
(double opt-in) before notifications are sent.

## Deployment (Hetzner / DigitalOcean)

1. Create a small Ubuntu VPS (Hetzner CX22 ~€4/month works).
2. Install: `apt install python3.11-venv git`. Clone this repo.
3. `pip install -r requirements.txt && python -m playwright install --with-deps chromium`.
4. Edit `config.yaml`. Set `app.base_url` to your domain (`https://termin.example.com`).
5. Put nginx in front for TLS (`certbot` is fine).
6. Two systemd units: one for `run_server.py`, one for `run_scraper.py`.

## Notes / caveats

- **Selectors will drift.** Government websites occasionally update their frontend. If the scraper starts returning 0 slots forever, re-run `discover.py`.
- **Gmail send quota:** ~500/day for a personal Gmail account. At scale, switch to Resend / SendGrid.
- **Be polite.** 60–120s polling with jitter is conservative; do not lower it. If the StädteRegion notices a single IP hammering them, they may block it.
- **This service is independent** of the StädteRegion Aachen and the Stadt Aachen. Make that clear in the Impressum (already done in `app/templates/impressum.html`).
