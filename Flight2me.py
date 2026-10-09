"""
Flight 2me  -  Doha <-> Beirut  (GitHub Actions version)
--------------------------------------------------------
GitHub Actions runs this every 10 minutes. It looks up DIRECT MEA
flights on your dates and sends notifications to your phone via ntfy:

  * First run       -> full list of all direct flights, times and prices
  * New flight      -> urgent alert for the new flight + the full list
  * Daily summary   -> full list once a day (time set in DAILY_SUMMARY_HOUR)

The full list is also saved in latest_flights.md in your repository,
so you can open it on GitHub at any time.

The ntfy topic is read from the repository secret NTFY_TOPIC.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fast_flights import FlightData, Passengers, get_flights

# ---------------- SETTINGS ----------------
SEARCHES = [
    ("DOH", "BEY", "2026-12-17"),
    ("DOH", "BEY", "2026-12-18"),
    ("BEY", "DOH", "2027-01-01"),
]

AIRLINE_KEYWORDS = ("MEA", "Middle East")  # only MEA flights count
DAILY_SUMMARY_HOUR = 9     # Qatar time (0-23); set to None to turn off
MAX_PRICE = None           # e.g. 900 -> also alert on any fare at or below this
# ------------------------------------------

QATAR = timezone(timedelta(hours=3))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
HERE = Path(__file__).parent
STATE_FILE = HERE / "mea_seen_flights.json"
REPORT_FILE = HERE / "latest_flights.md"


def notify(title, message, urgent=False):
    if not NTFY_TOPIC:
        print("  ! NTFY_TOPIC secret is missing, cannot notify")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "urgent" if urgent else "default",
                "Tags": "airplane",
                "Click": "https://www.mea.com.lb",
            },
            timeout=15,
        )
    except Exception as e:
        print("  ! notification failed:", e)


def price_number(price_text):
    digits = "".join(c for c in str(price_text) if c.isdigit())
    return int(digits) if digits else None


def is_direct(stops):
    if isinstance(stops, int):
        return stops == 0
    text = str(stops).strip().lower()
    if text in ("0", "nonstop", "non-stop", "direct"):
        return True
    if text.isdigit():
        return False
    return True  # unknown -> keep it, so a new flight is never missed


def fetch_direct_mea(origin, dest, date):
    result = get_flights(
        flight_data=[FlightData(date=date, from_airport=origin, to_airport=dest)],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="fallback",
    )
    flights = {}
    for f in result.flights:
        name = f.name or ""
        if not any(k.lower() in name.lower() for k in AIRLINE_KEYWORDS):
            continue
        if not is_direct(getattr(f, "stops", 0)):
            continue
        key = f"{name}|{f.departure}|{f.arrival}"
        flights[key] = {
            "dep": f.departure,
            "arr": f.arrival,
            "duration": getattr(f, "duration", "") or "",
            "price": f.price,
        }
    return flights


def flight_line(f):
    dur = f" ({f['duration']})" if f["duration"] else ""
    return f"• {f['dep']} → {f['arr']}{dur}  |  {f['price']}"


def full_list(state):
    parts = []
    for origin, dest, date in SEARCHES:
        route = f"{origin}->{dest} {date}"
        flights = state.get("routes", {}).get(route, {})
        parts.append(f"✈ {origin} → {dest}  {date}")
        if flights:
            ordered = sorted(flights.values(), key=lambda f: price_number(f["price"]) or 10**9)
            parts += [flight_line(f) for f in ordered]
        else:
            parts.append("• no direct MEA flights listed yet")
        parts.append("")
    return "\n".join(parts).strip()


def write_report(state):
    lines = ["# Flight 2me - direct MEA flights", ""]
    for origin, dest, date in SEARCHES:
        route = f"{origin}->{dest} {date}"
        flights = state.get("routes", {}).get(route, {})
        lines += [f"## {origin} → {dest}  {date}", "",
                  "| Departs | Arrives | Duration | Price |", "|---|---|---|---|"]
        if flights:
            for f in sorted(flights.values(), key=lambda f: price_number(f["price"]) or 10**9):
                lines.append(f"| {f['dep']} | {f['arr']} | {f['duration']} | {f['price']} |")
        else:
            lines.append("| no direct MEA flights listed yet | | | |")
        lines.append("")
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


def load_state():
    if not STATE_FILE.exists():
        return None
    data = json.loads(STATE_FILE.read_text())
    if "routes" not in data:          # older format from the first version
        data = {"routes": {}, "last_summary": ""}
    return data


def main():
    state = load_state()
    first_run_ever = state is None
    if first_run_ever:
        state = {"routes": {}, "last_summary": ""}

    new_flights = []
    for origin, dest, date in SEARCHES:
        route = f"{origin}->{dest} {date}"
        try:
            current = fetch_direct_mea(origin, dest, date)
        except Exception as e:
            print(f"  ! {route}: check failed ({e}), will retry next run")
            continue

        first_time = route not in state["routes"]
        seen = set(state.setdefault("seen", {}).get(route, []))

        for key, f in current.items():
            if key not in seen and not first_time:
                new_flights.append((route, f))
            p = price_number(f["price"])
            if MAX_PRICE and p and p <= MAX_PRICE:
                notify("Cheap MEA fare", f"{route}\n{flight_line(f)}", urgent=True)

        # "seen" remembers every flight ever found, so a flight that briefly
        # disappears (e.g. sold out) is not reported as new when it comes back.
        # "routes" holds only what is on sale right now, with current prices.
        state["seen"][route] = sorted(seen | set(current))
        state["routes"][route] = current
        print(f"  {route}: {len(current)} direct MEA flights"
              + (" (baseline saved)" if first_time else ""))
        for f in current.values():
            print("     ", flight_line(f))

    now = datetime.now(QATAR)
    today = now.strftime("%Y-%m-%d")

    if first_run_ever:
        notify("Flight 2me started", full_list(state))
        state["last_summary"] = today
    elif new_flights:
        new_text = "\n".join(f"{r}\n{flight_line(f)}" for r, f in new_flights)
        notify("NEW MEA flight! Book now",
               f"NEW:\n{new_text}\n\nALL DIRECT FLIGHTS:\n{full_list(state)}",
               urgent=True)
        print("  >>> NEW FLIGHT(S):", new_text.replace("\n", " | "))
    elif (DAILY_SUMMARY_HOUR is not None and now.hour >= DAILY_SUMMARY_HOUR
          and state.get("last_summary") != today):
        notify("Flight 2me daily summary", full_list(state))
        state["last_summary"] = today

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    write_report(state)


if __name__ == "__main__":
    main()
