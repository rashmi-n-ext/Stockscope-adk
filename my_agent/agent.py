from google.adk.agents.llm_agent import Agent
import requests
from typing import List, Dict, Any
from nsetools import Nse
from datetime import datetime
import schedule
from dotenv import load_dotenv
import os
import time
import re
import sys


INSTRUCTION = """You are an Indian stock market analysis assistant for a Telegram group.

Rules:
- You are NOT a financial advisor.
- Do NOT give direct commands like "buy now" or "sell everything".
- Explain:
  - What the current move might suggest (momentum, pullback, volatility) at a high level.
  - How a short-term trader vs a long-term investor might think about it.
  - Major risks (sector, market, news, valuation, liquidity).
- Use INR (₹) context.
- Use clear, practical language for Indian retail investors.
- ALWAYS end with this exact sentence:
  "This is not investment advice. Please do your own research and consider your risk profile."
Date: 27-Nov-2025

User question:
what about HCL

NSE data for mentioned symbols:
No valid NSE symbols resolved from the question.

Using ONLY this numeric context and generic market reasoning (no guarantees),
give a concise, practical analysis that covers:
- Brief snapshot of each stock.
- What the recent move may indicate.
- Short-term vs long-term considerations.
- Key risks / things to watch.

"""

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")     
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") 

raw_codes = Nse().get_stock_codes()

STOCK_CODES = {}


if isinstance(raw_codes, dict):
    # Old style: { 'SYMBOL': 'Company Name', ... }
    for sym, name in raw_codes.items():
        if sym == "SYMBOL":
            continue
        STOCK_CODES[str(sym).upper()] = str(name) if name is not None else ""
elif isinstance(raw_codes, list):
    # Newer / weird style: list of dicts or tuples
    for item in raw_codes:
        sym = None
        name = ""

        if isinstance(item, dict):
            sym = item.get("symbol") or item.get("SYMBOL")
            name = item.get("name") or item.get("NAME") or ""
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            sym, name = item[0], item[1]

        if sym:
            STOCK_CODES[str(sym).upper()] = str(name) if name is not None else ""
else:
    # Fallback – just in case
    STOCK_CODES = {}

ALL_SYMBOLS = set(STOCK_CODES.keys())

NAME_TO_SYMBOL = {
    name.lower(): sym
    for sym, name in STOCK_CODES.items()
    if isinstance(name, str) and name
}

def build_message(snapshot: Dict[str, List[Dict[str, Any]]]) -> str:
    gainers = snapshot.get("gainers", [])
    losers = snapshot.get("losers", [])

    now = datetime.now()
    header_time = now.strftime("%d-%b-%Y %H:%M")

    lines: List[str] = []
    lines.append(f"📊 *Indian Market Snapshot* (NSE)")
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

def summarize_bias(gainers: List[Dict[str, Any]], losers: List[Dict[str, Any]]) -> str:
    """Very rough 'market mood' line."""
    avg_g = sum(g["change_pct"] for g in gainers) / len(gainers) if gainers else 0
    avg_l = sum(l["change_pct"] for l in losers) / len(losers) if losers else 0

    if avg_g > abs(avg_l):
        return "Bias: 🟢 Gainers are stronger in this snapshot."
    if abs(avg_l) > avg_g:
        return "Bias: 🔴 Losers are stronger in this snapshot."
    return "Bias: ⚪ Mixed / balanced snapshot."

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

def fetch_trending() -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch top gainers & losers from NSE using nsetools.
    Handles both old and new field names.
    """
    try:
        # Explicitly ask for NIFTY; you can change to "ALL" or others later
        gainers_raw = Nse().get_top_gainers(index="NIFTY") or []
        losers_raw  = Nse().get_top_losers(index="NIFTY") or []

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

    gainers = [normalize(x) for x in gainers_raw][:5]
    losers  = [normalize(x) for x in losers_raw][:5]

    # Filter only truly broken rows
    gainers = [g for g in gainers if g["symbol"] is not None and g["change_pct"] is not None]
    losers  = [l for l in losers if l["symbol"] is not None and l["change_pct"] is not None]

    print("NORMALIZED LENGTHS:", len(gainers), len(losers))

    return {"gainers": gainers, "losers": losers}

def send_telegram_message(text: str) -> None:
    token = TELEGRAM_BOT_TOKEN.strip()
    chat_id = TELEGRAM_CHAT_ID.strip()
    print("sending telegram message..")

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

def guess_symbols_from_question(text: str) -> list[str]:
    """Resolve NSE symbols or company names from a free-form question."""
    text_clean = text.strip()
    if not text_clean:
        return []

    # Token-based symbol detection: TCS, HDFCBANK, SBIN, etc.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]{1,10}", text_clean)
    candidates = set(t.upper() for t in tokens)

    found = []

    for sym in candidates:
        if sym in ALL_SYMBOLS:
            found.append(sym)

    # If no direct symbol match, try company names
    if not found:
        text_lower = text_clean.lower()
        for name, sym in NAME_TO_SYMBOL.items():
            if name in text_lower:
                found.append(sym)

    # Deduplicate, preserve order
    unique = []
    for s in found:
        if s not in unique:
            unique.append(s)

    return unique

def get_stock_snapshot(symbol: str) -> dict:
    q = Nse().get_quote(symbol)
    if not q:
        return {}

    def to_float(val):
        try:
            return float(str(val).replace(",", ""))
        except Exception:
            return None

    return {
        "symbol": symbol,
        "company": STOCK_CODES.get(symbol, ""),
        "lastPrice": to_float(q.get("lastPrice")),
        "pChange": to_float(q.get("pChange") or q.get("perChange")),
        "previousClose": to_float(q.get("previousClose")),
        "open": to_float(q.get("open")),
        "dayHigh": to_float(q.get("dayHigh")),
        "dayLow": to_float(q.get("dayLow")),
        "totalTradedVolume": q.get("totalTradedVolume"),
    }

def handle_ai_question(user_question: str) -> str:
    symbols = guess_symbols_from_question(user_question)

    snapshots = []
    for sym in symbols:
        snap = get_stock_snapshot(sym)
        if snap:
            snapshots.append(snap)

    if not snapshots:
        context_str = "No valid NSE symbols resolved from the question."
    else:
        lines = []
        for s in snapshots:
            lines.append(
                f"{s['symbol']} ({s['company']}): "
                f"Price ₹{s['lastPrice']}, "
                f"Change {s['pChange']}%, "
                f"PrevClose ₹{s['previousClose']}, "
                f"DayRange ₹{s['dayLow']} - ₹{s['dayHigh']}, "
                f"Volume {s['totalTradedVolume']}"
            )
        context_str = "\n".join(lines)

    today = datetime.now().strftime("%d-%b-%Y")

    system_prompt = """
You are an Indian stock market analysis assistant for a Telegram group.

Rules:
- You are NOT a financial advisor.
- Do NOT give direct commands like "buy now" or "sell everything".
- Explain:
  - What the current move might suggest (momentum, pullback, volatility) at a high level.
  - How a short-term trader vs a long-term investor might think about it.
  - Major risks (sector, market, news, valuation, liquidity).
- Use INR (₹) context.
- Use clear, practical language for Indian retail investors.
- ALWAYS end with this exact sentence:
  "This is not investment advice. Please do your own research and consider your risk profile."
""".strip()

    user_prompt = f"""
Date: {today}

User question:
{user_question}

NSE data for mentioned symbols:
{context_str}

Using ONLY this numeric context and generic market reasoning (no guarantees),
give a concise, practical analysis that covers:
- Brief snapshot of each stock.
- What the recent move may indicate.
- Short-term vs long-term considerations.
- Key risks / things to watch.
""".strip()
    print(system_prompt)
    print(user_prompt)
    response = root_agent([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return user_question


def market_bot_tool(user_question: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch top gainers & losers from NSE using nsetools.
    Handles both old and new field names.
    runs your AI analysis prompt.
    """
    snapshot = fetch_trending()
    print("DEBUG SNAPSHOT:", snapshot)
    msg = build_message(snapshot)
    print("DEBUG MSG:", repr(msg))

    return {
        "DEBUG SNAPSHOT:": repr(msg)
    }

root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction=INSTRUCTION,
    tools=[market_bot_tool,
    handle_ai_question],  # ✅ register your tool here
)

if __name__ == "__main__":
    # ✅ Start the ADK web server
    root_agent.run_web()