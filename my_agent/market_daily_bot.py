#!/usr/bin/env python3
"""
Indian Market Daily Snapshot -> Telegram (Hackathon Edition)

- No .env, all config hardcoded here.
- Uses nsetools to fetch NSE top gainers & losers.
- Sends a single formatted message to a Telegram group.

Run:
    python market_daily_bot.py
"""

# =========================
# Module 1: Imports & Config
# =========================

import requests
from nsetools import Nse
from datetime import datetime
from typing import List, Dict, Any
import time
import sys
import schedule
from dotenv import load_dotenv
import os

load_dotenv()


# ---------- HARD-CODED CONFIG (EDIT THESE) ----------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")     
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")     

TOP_N = 5  # number of gainers/losers to display
EXCHANGE_LABEL = "NSE"  # just a label in the message


# ---------- ALERT CONFIG ----------

# Stock symbols to watch (all NSE symbols, same as you'd pass to nsetools.get_quote)
WATCHLIST = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]

# Fire alert when absolute % change >= this
ALERT_PERCENT_THRESHOLD = 0.0  # e.g. 3% move

# How often to check (in minutes) in --alerts mode
ALERT_INTERVAL_MIN = 1

# Only one alert per stock per day
ALERT_ONCE_PER_DAY = True

# Always send watchlist snapshot every cycle?
ALWAYS_SEND_WATCHLIST_SNAPSHOT = True
# ----------------------------------------------------
# ---------------------------------------------------

if TELEGRAM_BOT_TOKEN.startswith("PUT_") or TELEGRAM_CHAT_ID.startswith("PUT_"):
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID at top of file.")

nse = Nse()

# =========================
# Module 2: Market Data (NSE)
# =========================

def fetch_trending() -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch top gainers & losers from NSE using nsetools.
    Handles both old and new field names.
    """
    try:
        # Explicitly ask for NIFTY; you can change to "ALL" or others later
        gainers_raw = nse.get_top_gainers(index="NIFTY") or []
        losers_raw  = nse.get_top_losers(index="NIFTY") or []

        print("RAW LENGTHS:", len(gainers_raw), len(losers_raw))
        # Uncomment if you want to see sample:
        # if gainers_raw:
        #     print("SAMPLE GAINER:", gainers_raw[0])
    except Exception as e:
        print("ERROR fetching NSE data:", e)
        return {"gainers": [], "losers": []}

    def normalize(item: Dict[str, Any]) -> Dict[str, Any]:
        symbol = item.get("symbol")

        # price fields from nsetools
        price = item.get("ltp")
        change_pct = (
            item.get("perChange")
            or item.get("pChange")
            or item.get("netPrice")
            or item.get("net_price")
        )
        volume = item.get("tradedQuantity") or 0

        day_high = item.get("highPrice")
        day_low = item.get("lowPrice")

        def to_float(val):
            try:
                return float(str(val).replace(",", ""))
            except Exception:
                return None

        def to_int(val):
            try:
                return int(str(val).replace(",", ""))
            except Exception:
                return 0

        return {
            "symbol": symbol,
            "price": to_float(price),
            "change_pct": to_float(change_pct),
            "volume": to_int(volume),
            "day_high": to_float(day_high),
            "day_low": to_float(day_low),
        }

    gainers = [normalize(x) for x in gainers_raw][:TOP_N]
    losers  = [normalize(x) for x in losers_raw][:TOP_N]

    # Filter only truly broken rows
    gainers = [g for g in gainers if g["symbol"] is not None and g["change_pct"] is not None]
    losers  = [l for l in losers if l["symbol"] is not None and l["change_pct"] is not None]

    print("NORMALIZED LENGTHS:", len(gainers), len(losers))

    return {"gainers": gainers, "losers": losers}

# =========================
# Module 3: "Next Move" Tags
# =========================

def classify_stock(change_pct: float, volume: int) -> str:
    """
    Ultra-simple explanation tag. Not advice. Just English on top of % move.
    """
    if change_pct >= 8:
        return "🔴 Extended move up, high volatility / pullback risk"
    if 5 <= change_pct < 8:
        if volume > 1_000_000:
            return "⚡ Strong momentum with volume, possible continuation"
        else:
            return "📈 Breakout-like move, watch next sessions"
    if 2 <= change_pct < 5:
        return "⬆️ Healthy up-move, potential trend continuation"
    if -2 < change_pct < 2:
        return "➖ Sideways / indecisive day"

    if -5 <= change_pct <= -2:
        return "📉 Normal pullback, check support & key supports"
    if change_pct < -5:
        return "🚨 Sharp selloff, very risky / possible oversold bounce"

    return "❓ No clear pattern, watch broader context"

def compute_future_bias(s: Dict[str, Any]) -> str:
    """
    Heuristic "future sentiment" based on today's move + position in day range.

    This is NOT a prediction, just a label like:
    - Short-term bullish momentum
    - Momentum tiring
    - Oversold bounce candidate
    """
    change = s.get("change_pct") or 0.0
    vol = s.get("volume") or 0
    price = s.get("price")
    high = s.get("day_high")
    low = s.get("day_low")

    # Where is the close inside today's range? 0 = near low, 1 = near high
    range_pos = None
    if (
        price is not None
        and high is not None
        and low is not None
        and high > low
    ):
        range_pos = (price - low) / (high - low)

    bias = "Sideways / noisy"
    risk = "Medium"

    # Big up move
    if change >= 5:
        risk = "High"
        if range_pos is not None and range_pos > 0.7:
            bias = "Short-term bullish momentum"
        else:
            bias = "Momentum tiring / late entry risk"

    # Moderate up move
    elif 2 <= change < 5:
        if range_pos is not None and range_pos > 0.6:
            bias = "Gradual uptrend, dips can be watched"
            risk = "Medium"
        else:
            bias = "Up but sellers active intraday"
            risk = "Medium"

    # Flat-ish
    elif -2 < change < 2:
        bias = "Indecisive / consolidation"
        risk = "Low"

    # Moderate down move
    elif -5 <= change <= -2:
        if range_pos is not None and range_pos < 0.4:
            bias = "Oversold bounce candidate (still risky)"
            risk = "High"
        else:
            bias = "Normal pullback inside trend"
            risk = "Medium"

    # Big down move
    elif change < -5:
        bias = "Panic selloff / very high risk zone"
        risk = "Very high"

    return f"{bias} | Risk: {risk}"


def summarize_bias(gainers: List[Dict[str, Any]], losers: List[Dict[str, Any]]) -> str:
    """Very rough 'market mood' line."""
    avg_g = sum(g["change_pct"] for g in gainers) / len(gainers) if gainers else 0
    avg_l = sum(l["change_pct"] for l in losers) / len(losers) if losers else 0

    if avg_g > abs(avg_l):
        return "Bias: 🟢 Gainers are stronger in this snapshot."
    if abs(avg_l) > avg_g:
        return "Bias: 🔴 Losers are stronger in this snapshot."
    return "Bias: ⚪ Mixed / balanced snapshot."

# =========================
# Module 4: Message Builder
# =========================

def format_stock_line(idx: int, s: Dict[str, Any]) -> str:
    symbol = s["symbol"]
    price = s["price"]
    change = s["change_pct"]
    vol = s["volume"]

    tag = classify_stock(change, vol)
    sentiment = compute_future_bias(s)

    price_str = f"₹{price:,.2f}" if price is not None else "N/A"

    return (
        f"{idx}. *{symbol}*  {change:+.2f}%  ({price_str})\n"
        f"   {tag}\n"
        f"   Sentiment: {sentiment}"
    )


def build_message(snapshot: Dict[str, List[Dict[str, Any]]]) -> str:
    gainers = snapshot.get("gainers", [])
    losers = snapshot.get("losers", [])

    now = datetime.now()
    header_time = now.strftime("%d-%b-%Y %H:%M")

    lines: List[str] = []
    lines.append(f"📊 *Indian Market Snapshot* ({EXCHANGE_LABEL})")
    lines.append(f"_As of {header_time}_")
    lines.append("")

    if gainers:
        lines.append("🟢 *Top Gainers*")
        for i, g in enumerate(gainers, start=1):
            lines.append(format_stock_line(i, g))
        lines.append("")

    if losers:
        lines.append("🔴 *Top Losers*")
        for i, l in enumerate(losers, start=1):
            lines.append(format_stock_line(i, l))
        lines.append("")

    if gainers and losers:
        lines.append(summarize_bias(gainers, losers))
        lines.append("")

    lines.append("_Auto-generated for study & monitoring only. Not investment advice._")

    return "\n".join(lines)

# =========================
# Module 5: Telegram Sender
# =========================

def send_telegram_message(text: str) -> None:
    token = TELEGRAM_BOT_TOKEN.strip()
    chat_id = TELEGRAM_CHAT_ID.strip()

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Debug if needed:
    # print("DEBUG URL:", repr(url))

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    resp = requests.post(url, json=payload, timeout=15)
    # print("DEBUG STATUS:", resp.status_code, "BODY:", resp.text[:200])

    if resp.status_code != 200:
        raise RuntimeError(f"Telegram error: {resp.status_code} {resp.text[:200]}")

# =========================
# Module 6: Orchestration
# =========================

def run_once() -> None:
    snapshot = fetch_trending()
    print("DEBUG SNAPSHOT:", snapshot)

    msg = build_message(snapshot)
    print("DEBUG MSG:", repr(msg))

    send_telegram_message(msg)

# =========================
# Module 7: Watchlist Alerts
# =========================

# In-memory alert state so we don't spam same stock all day
_alert_state = {}  # symbol -> "YYYY-MM-DD"


def _to_float(val):
    try:
        return float(str(val).replace(",", ""))
    except Exception:
        return None


def check_watchlist_alerts() -> None:
    """
    Check watchlist symbols, send alert/snapshot message.

    - Uses nse.get_quote(symbol)
    - Marks symbols crossing ALERT_PERCENT_THRESHOLD
    - If ALWAYS_SEND_WATCHLIST_SNAPSHOT = True:
        - Always sends list with all watchlist stocks + markers
      Else:
        - Sends only when at least one symbol crosses the threshold
    """
    if not WATCHLIST:
        print("WATCHLIST is empty, skip alerts.")
        return

    from datetime import datetime

    today_key = datetime.now().strftime("%Y-%m-%d")
    rows = []        # [(symbol, pchange, last_price, is_triggered)]
    any_triggered = False

    for symbol in WATCHLIST:
        try:
            quote = nse.get_quote(symbol)
        except Exception as e:
            print(f"ERROR fetching quote for {symbol}: {e}")
            continue

        # nsetools quote fields: usually 'pChange' (% change) and 'lastPrice'
        raw_pchange = (
            quote.get("pChange")
            or quote.get("perChange")
            or quote.get("netPrice")
            or quote.get("net_price")
        )
        raw_last = quote.get("lastPrice") or quote.get("ltp")

        pchange = _to_float(raw_pchange)
        last_price = _to_float(raw_last)

        print(f"[DEBUG] {symbol} raw_pchange={raw_pchange}, pchange={pchange}, last={last_price}")

        if pchange is None:
            continue

        is_triggered = False

        # Threshold check
        if abs(pchange) >= ALERT_PERCENT_THRESHOLD:
            # Once-per-day guard
            if not (ALERT_ONCE_PER_DAY and _alert_state.get(symbol) == today_key):
                is_triggered = True
                _alert_state[symbol] = today_key

        if is_triggered:
            any_triggered = True

        rows.append((symbol, pchange, last_price, is_triggered))

    # No usable data at all
    if not rows:
        print("No quotes for watchlist, skip.")
        return

    if not any_triggered and not ALWAYS_SEND_WATCHLIST_SNAPSHOT:
        print("No alerts this cycle (and snapshot disabled).")
        return

    # Build message
    now_str = datetime.now().strftime("%d-%b-%Y %H:%M")
    header = (
        f"⚡ *Watchlist Alerts* (±{ALERT_PERCENT_THRESHOLD:.1f}% or more) ({EXCHANGE_LABEL})"
        if any_triggered
        else f"📌 *Watchlist Snapshot* ({EXCHANGE_LABEL})"
    )

    lines = []
    lines.append(header)
    lines.append(f"_As of {now_str}_")
    lines.append("")

    for symbol, pchange, last_price, is_triggered in rows:
        price_str = f"₹{last_price:,.2f}" if last_price is not None else "N/A"
        marker = "🔥" if is_triggered else "•"
        lines.append(
            f"{marker} *{symbol}* {pchange:+.2f}% ({price_str})"
        )

    lines.append("")
    if any_triggered:
        lines.append(
            "_Auto-generated watchlist alerts for study & monitoring only. Not investment advice._"
        )
    else:
        lines.append(
            "_No symbol crossed the alert threshold yet. Snapshot for monitoring only._"
        )

    alert_msg = "\n".join(lines)
    send_telegram_message(alert_msg)
    print(f"Sent snapshot with {len(rows)} stock(s). Triggered: {any_triggered}")



# =========================
# Module 8: CLI entrypoint
# =========================

def main():
    """
    Usage:
      python MarketUpdates.py           -> single daily snapshot (once)
      python MarketUpdates.py --alerts  -> run watchlist alerts loop
    """
    if len(sys.argv) > 1 and sys.argv[1] == "--alerts":
        print(
            f"Starting ALERT mode every {ALERT_INTERVAL_MIN} minutes "
            f"for watchlist: {WATCHLIST}"
        )
        # Run once immediately
        check_watchlist_alerts()
        # Schedule future checks
        schedule.every(ALERT_INTERVAL_MIN).minutes.do(check_watchlist_alerts)

        while True:
            schedule.run_pending()
            time.sleep(5)
    else:
        run_once()
        print("Market snapshot sent to Telegram.")


if __name__ == "__main__":
    main()