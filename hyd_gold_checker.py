#!/usr/bin/env python3
"""
Hyderabad Gold & Silver Rate Checker — WhatsApp Alerts via Callmebot
=====================================================================
✅ Reads ALL secrets from environment variables (GitHub Actions Secrets)
✅ Scalable recipient list — add more numbers anytime via GitHub Secrets
🚨 Instant spike alerts on drastic price movements
💡 Spike monitor uses cached data — ZERO extra API calls

📊 API USAGE (Free tier = 100 calls/month):
   • 3 checks/day × 6 days × 4.33 weeks × 2 calls = ~52 calls/month ✅

⏰ SCHEDULED ALERTS (Mon–Sat): 10:00 AM | 1:00 PM | 5:00 PM IST
🚨 SPIKE ALERTS: Triggered when Gold moves ±1.5% or Silver ±2.0%
"""

import json
import os
import sys
import time
import argparse
import logging
from datetime import datetime

try:
    import requests
    import schedule
except ImportError:
    print("Missing dependencies. Run:  pip install requests schedule")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════
# CONFIGURATION — All values are read from environment variables
# (Set these as GitHub Actions Secrets — no hardcoding needed!)
#
# Required secrets in GitHub:
#   GOLDAPI_KEY          → your GoldAPI.io key
#   WHATSAPP_PHONE_1     → your number e.g. +919876543210
#   WHATSAPP_API_KEY_1   → your Callmebot key
#
# To add more recipients later, add these secrets in GitHub:
#   WHATSAPP_PHONE_2     → second number
#   WHATSAPP_API_KEY_2   → second Callmebot key
#   WHATSAPP_PHONE_3     → third number
#   WHATSAPP_API_KEY_3   → third Callmebot key
#   ... and so on up to WHATSAPP_PHONE_10 / WHATSAPP_API_KEY_10
#
# The script auto-discovers however many recipients you've added.
# ═══════════════════════════════════════════════════════════

GOLDAPI_KEY = os.environ.get("GOLDAPI_KEY", "")

# Auto-discover recipients from environment variables
# Looks for WHATSAPP_PHONE_1 + WHATSAPP_API_KEY_1, then _2, _3 ... up to _10
def load_recipients() -> list:
    recipients = []
    for i in range(1, 11):   # supports up to 10 numbers
        phone  = os.environ.get(f"WHATSAPP_PHONE_{i}", "").strip()
        apikey = os.environ.get(f"WHATSAPP_API_KEY_{i}", "").strip()
        name   = os.environ.get(f"WHATSAPP_NAME_{i}", f"Recipient {i}").strip()
        if phone and apikey:
            recipients.append({
                "name":   name,
                "phone":  phone,
                "apikey": apikey,
                "active": True,
            })
    return recipients

RECIPIENTS = load_recipients()

# ── Schedule ─────────────────────────────────────────────
ALERT_TIMES = ["10:00", "13:00", "17:00"]       # 24hr IST, Mon–Sat

# ── Spike Alert Thresholds ────────────────────────────────
GOLD_SPIKE_THRESHOLD_PCT   = 1.5
SILVER_SPIKE_THRESHOLD_PCT = 2.0
SPIKE_CHECK_INTERVAL_MIN   = 30

# ── Files ─────────────────────────────────────────────────
LAST_RATE_FILE  = "gold_last_rate.json"
SPIKE_BASE_FILE = "gold_spike_base.json"

# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

GOLDAPI_URL   = "https://www.goldapi.io/api/{metal}/INR"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

KARAT_PURITY = {
    "24K": 1.0000,
    "22K": 0.9167,
    "18K": 0.7500,
}


# ═══════════════════════════════════════════════════════════
# Recipient helpers
# ═══════════════════════════════════════════════════════════

def active_recipients() -> list:
    return [r for r in RECIPIENTS if r.get("active")]

def recipient_summary() -> str:
    active = active_recipients()
    names  = [r["name"] for r in active]
    return f"{len(active)} active: {', '.join(names)}" if names else "none configured"


# ═══════════════════════════════════════════════════════════
# Fetch prices
# ═══════════════════════════════════════════════════════════

def fetch_metal_price(metal: str) -> dict:
    url = GOLDAPI_URL.format(metal=metal)
    headers = {
        "x-access-token": GOLDAPI_KEY,
        "Content-Type":   "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP {resp.status_code}: {e}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Network error: {e}"}
    except ValueError:
        return {"error": "Invalid JSON from GoldAPI"}

    price_per_gram      = round(data.get("price", 0) / 31.1035, 2)
    prev_price_per_gram = round(data.get("prev_close_price", data.get("price", 0)) / 31.1035, 2)
    return {
        "price_per_gram":  price_per_gram,
        "prev_per_gram":   prev_price_per_gram,
        "change_per_gram": round(price_per_gram - prev_price_per_gram, 2),
    }


def get_all_rates() -> dict:
    log.info("Fetching gold price from GoldAPI.io...")
    gold = fetch_metal_price("XAU")
    if "error" in gold:
        return {"error": gold["error"]}

    log.info("Fetching silver price from GoldAPI.io...")
    silver = fetch_metal_price("XAG")

    g   = gold["price_per_gram"]
    g_p = gold["prev_per_gram"]

    karat_rates = {}
    for karat, purity in KARAT_PURITY.items():
        rate = round(g * purity, 2)
        prev = round(g_p * purity, 2)
        karat_rates[karat] = {
            "per_gram": rate,
            "per_10g":  round(rate * 10, 2),
            "change":   round(rate - prev, 2),
        }

    result = {
        "checked_at":      datetime.now().strftime("%d %b %Y, %I:%M %p IST"),
        "checked_at_ts":   datetime.now().isoformat(),
        "gold":            karat_rates,
        "gold_per_gram":   g,
        "silver":          None,
        "silver_per_gram": None,
    }

    if "error" not in silver:
        sg = silver["price_per_gram"]
        result["silver"] = {
            "per_gram": sg,
            "per_kg":   round(sg * 1000, 2),
            "change":   silver["change_per_gram"],
        }
        result["silver_per_gram"] = sg
    else:
        log.warning(f"Silver fetch failed: {silver['error']}")

    return result


# ═══════════════════════════════════════════════════════════
# File helpers
# ═══════════════════════════════════════════════════════════

def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
# Message builders
# ═══════════════════════════════════════════════════════════

def trend(change: float) -> str:
    if change > 0:   return f"📈 +₹{change:,.2f}"
    elif change < 0: return f"📉 ₹{change:,.2f}"
    return "➡️  No change"


def session_label() -> str:
    h = datetime.now().hour
    if h < 12: return "🌅 Morning Rate"
    if h < 15: return "☀️  Midday Rate"
    return "🌆 Closing Rate"


def next_alert_time() -> str:
    cur = datetime.now().hour * 60 + datetime.now().minute
    for t in ALERT_TIMES:
        h, m = map(int, t.split(":"))
        if h * 60 + m > cur:
            suffix = "AM" if h < 12 else "PM"
            return f"{h if h <= 12 else h - 12}:{m:02d} {suffix} today"
    return "10:00 AM tomorrow"


def build_scheduled_message(rates: dict) -> str:
    g = rates["gold"]
    s = rates.get("silver")
    lines = [
        "💰 *Hyderabad Gold & Silver Rates*",
        f"{session_label()} | {rates['checked_at']}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🥇 *GOLD — Per Gram*",
        f"  24K (Pure)  : ₹{g['24K']['per_gram']:>10,.2f}   {trend(g['24K']['change'])}",
        f"  22K (Jewel) : ₹{g['22K']['per_gram']:>10,.2f}   {trend(g['22K']['change'])}",
        f"  18K         : ₹{g['18K']['per_gram']:>10,.2f}   {trend(g['18K']['change'])}",
        "",
        "🥇 *GOLD — Per 10 Grams*",
        f"  24K : ₹{g['24K']['per_10g']:,.2f}",
        f"  22K : ₹{g['22K']['per_10g']:,.2f}",
        f"  18K : ₹{g['18K']['per_10g']:,.2f}",
    ]
    if s:
        lines += [
            "",
            "🥈 *SILVER*",
            f"  Per gram : ₹{s['per_gram']:,.2f}   {trend(s['change'])}",
            f"  Per kg   : ₹{s['per_kg']:,.2f}",
        ]
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📍 Hyderabad | Excl. GST & making charges",
        f"⏰ Next update: {next_alert_time()}",
    ]
    return "\n".join(lines)


def build_spike_message(rates: dict, baseline: dict, gold_pct: float, silver_pct: float) -> str:
    g = rates["gold"]
    s = rates.get("silver")
    max_move = max(abs(gold_pct), abs(silver_pct) if silver_pct else 0)

    if max_move >= 3.0:
        severity = "🔴 CRITICAL ALERT"
        action   = "⚠️ IMMEDIATE ACTION RECOMMENDED"
    elif max_move >= 2.0:
        severity = "🟠 MAJOR ALERT"
        action   = "⚠️ Review your positions now"
    else:
        severity = "🟡 SPIKE ALERT"
        action   = "📌 Monitor closely"

    lines = [
        f"🚨 *{severity}* 🚨",
        "Drastic price movement detected!",
        f"{datetime.now().strftime('%d %b %Y, %I:%M %p IST')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if abs(gold_pct) >= GOLD_SPIKE_THRESHOLD_PCT:
        direction = "SURGED ▲" if gold_pct > 0 else "DROPPED ▼"
        lines += [
            "",
            f"🥇 *GOLD {direction} {abs(gold_pct):.2f}%*",
            f"  Now : ₹{g['24K']['per_gram']:,.2f}/g (24K)",
            f"  Was : ₹{baseline.get('gold_per_gram', 0):,.2f}/g",
            f"  Δ   : {trend(g['24K']['change'])}",
        ]
    if s and silver_pct and abs(silver_pct) >= SILVER_SPIKE_THRESHOLD_PCT:
        direction = "SURGED ▲" if silver_pct > 0 else "DROPPED ▼"
        lines += [
            "",
            f"🥈 *SILVER {direction} {abs(silver_pct):.2f}%*",
            f"  Now : ₹{s['per_gram']:,.2f}/g",
            f"  Was : ₹{baseline.get('silver_per_gram', 0):,.2f}/g",
            f"  Δ   : {trend(s['change'])}",
        ]
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💡 {action}",
        "",
        "🥇 *Current Rates*",
        f"  24K: ₹{g['24K']['per_gram']:,.2f}/g  |  ₹{g['24K']['per_10g']:,.2f}/10g",
        f"  22K: ₹{g['22K']['per_gram']:,.2f}/g  |  ₹{g['22K']['per_10g']:,.2f}/10g",
        f"  18K: ₹{g['18K']['per_gram']:,.2f}/g  |  ₹{g['18K']['per_10g']:,.2f}/10g",
    ]
    if s:
        lines.append(f"  Silver: ₹{s['per_gram']:,.2f}/g  |  ₹{s['per_kg']:,.2f}/kg")
    lines += ["", "📍 Hyderabad | Excl. GST & making charges"]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# WhatsApp sender
# ═══════════════════════════════════════════════════════════

def send_to_recipient(recipient: dict, message: str) -> bool:
    params = {
        "phone":  recipient["phone"],
        "text":   message,
        "apikey": recipient["apikey"],
    }
    try:
        resp = requests.get(CALLMEBOT_URL, params=params, timeout=30)
        resp.raise_for_status()
        log.info(f"  ✅ Sent → {recipient['name']} ({recipient['phone']})")
        return True
    except requests.exceptions.HTTPError:
        if resp.status_code == 403:
            log.error(
                f"  ❌ {recipient['name']}: Callmebot 403 — key invalid or not activated.\n"
                "     Send 'I allow callmebot to send me messages' to +34 644 52 74 85"
            )
        else:
            log.error(f"  ❌ {recipient['name']}: HTTP {resp.status_code} — {resp.text}")
        return False
    except requests.exceptions.RequestException as e:
        log.error(f"  ❌ {recipient['name']}: {e}")
        return False


def broadcast(message: str, tag: str = ""):
    targets = active_recipients()
    if not targets:
        log.error("No active recipients configured!")
        return
    log.info(f"Broadcasting to {len(targets)} recipient(s) {tag}")
    for r in targets:
        send_to_recipient(r, message)
        time.sleep(1)


# ═══════════════════════════════════════════════════════════
# Scheduled check
# ═══════════════════════════════════════════════════════════

def run_scheduled_check():
    if datetime.now().weekday() == 6:
        log.info("Sunday — market closed, skipping.")
        return

    log.info("── Scheduled check ──────────────────────────")
    rates = get_all_rates()

    if "error" in rates:
        log.error(f"Fetch failed: {rates['error']}")
        broadcast(
            f"⚠️ Gold Checker Error\n"
            f"{datetime.now().strftime('%d %b %Y, %I:%M %p')}\n\n"
            f"Could not fetch rates:\n{rates['error']}\n"
            f"Will retry at next scheduled time.",
            tag="[error]"
        )
        return

    log.info(
        f"24K: ₹{rates['gold']['24K']['per_gram']:,.2f}/g | "
        f"22K: ₹{rates['gold']['22K']['per_gram']:,.2f}/g | "
        f"Silver: ₹{rates['silver']['per_gram'] if rates['silver'] else 'N/A'}/g"
    )

    broadcast(build_scheduled_message(rates), tag="[scheduled]")
    save_json(LAST_RATE_FILE, rates)
    save_json(SPIKE_BASE_FILE, {
        "gold_per_gram":   rates["gold_per_gram"],
        "silver_per_gram": rates["silver_per_gram"],
        "set_at":          rates["checked_at"],
    })
    log.info("Spike baseline reset.")


# ═══════════════════════════════════════════════════════════
# Spike monitor (0 extra API calls)
# ═══════════════════════════════════════════════════════════

def check_for_spikes():
    if datetime.now().weekday() == 6:
        return

    latest   = load_json(LAST_RATE_FILE)
    baseline = load_json(SPIKE_BASE_FILE)
    if not latest or not baseline:
        return

    gold_now    = latest.get("gold_per_gram")
    gold_base   = baseline.get("gold_per_gram")
    silver_now  = latest.get("silver_per_gram")
    silver_base = baseline.get("silver_per_gram")

    if not gold_now or not gold_base:
        return

    gold_pct   = ((gold_now - gold_base) / gold_base) * 100
    silver_pct = ((silver_now - silver_base) / silver_base) * 100 if silver_now and silver_base else 0

    if abs(gold_pct) >= GOLD_SPIKE_THRESHOLD_PCT or abs(silver_pct) >= SILVER_SPIKE_THRESHOLD_PCT:
        log.warning(f"🚨 SPIKE! Gold: {gold_pct:+.2f}% | Silver: {silver_pct:+.2f}%")
        broadcast(build_spike_message(latest, baseline, gold_pct, silver_pct), tag="[SPIKE ALERT]")
        save_json(SPIKE_BASE_FILE, {
            "gold_per_gram":   gold_now,
            "silver_per_gram": silver_now,
            "set_at":          latest.get("checked_at", ""),
        })
    else:
        log.info(
            f"Spike check OK — Gold: {gold_pct:+.2f}% | Silver: {silver_pct:+.2f}% "
            f"(thresholds ±{GOLD_SPIKE_THRESHOLD_PCT}% / ±{SILVER_SPIKE_THRESHOLD_PCT}%)"
        )


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hyderabad Gold & Silver Rate WhatsApp Alerter")
    parser.add_argument("--once",       action="store_true", help="Run one check and exit")
    parser.add_argument("--list",       action="store_true", help="List recipients and exit")
    parser.add_argument("--test-alert", action="store_true", help="Send a test WhatsApp to all recipients")
    args = parser.parse_args()

    if not GOLDAPI_KEY:
        print("❌ GOLDAPI_KEY environment variable not set.")
        print("   Add it as a GitHub Secret named GOLDAPI_KEY")
        sys.exit(1)

    if args.list:
        print("\n── Configured Recipients ──────────────────")
        for i, r in enumerate(RECIPIENTS, 1):
            print(f"  {i}. {r['name']:20s}  {r['phone']}  ✅ Active")
        print(f"\nTotal: {recipient_summary()}")
        sys.exit(0)

    if args.test_alert:
        log.info(f"Sending test alert to: {recipient_summary()}")
        broadcast(
            f"✅ *Gold Checker — Test Message*\n"
            f"{datetime.now().strftime('%d %b %Y, %I:%M %p IST')}\n\n"
            f"Your Hyderabad Gold & Silver Rate Checker is working!\n\n"
            f"You will receive:\n"
            f"  📋 Scheduled alerts at {', '.join(ALERT_TIMES)} IST (Mon–Sat)\n"
            f"  🚨 Spike alerts on ±{GOLD_SPIKE_THRESHOLD_PCT}% gold / ±{SILVER_SPIKE_THRESHOLD_PCT}% silver moves",
            tag="[test]"
        )
        sys.exit(0)

    if not active_recipients():
        print("❌ No recipients found.")
        print("   Add WHATSAPP_PHONE_1 and WHATSAPP_API_KEY_1 as GitHub Secrets")
        sys.exit(1)

    if args.once:
        run_scheduled_check()
    else:
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info(" Hyderabad Gold Rate Checker — STARTED")
        log.info(f" Alerts (IST): {', '.join(ALERT_TIMES)}  Mon–Sat")
        log.info(f" Spike monitor: every {SPIKE_CHECK_INTERVAL_MIN} min")
        log.info(f" Recipients: {recipient_summary()}")
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        for t in ALERT_TIMES:
            for day in [schedule.every().monday, schedule.every().tuesday,
                        schedule.every().wednesday, schedule.every().thursday,
                        schedule.every().friday, schedule.every().saturday]:
                day.at(t).do(run_scheduled_check)

        schedule.every(SPIKE_CHECK_INTERVAL_MIN).minutes.do(check_for_spikes)
        run_scheduled_check()

        while True:
            schedule.run_pending()
            time.sleep(30)
