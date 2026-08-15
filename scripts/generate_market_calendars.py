#!/usr/bin/env python3
"""Generate a Flutter-friendly global exchange holiday calendar JSON file."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


API_BASE_URL = os.environ.get(
    "TRADING_CALENDAR_API_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

OUTPUT_PATH = Path(
    os.environ.get("MARKET_CALENDAR_OUTPUT", "data/market_calendars.json")
)

MARKETS = {
    "usa": {"mic": "XNYS", "timezone": "America/New_York"},
    "korea": {"mic": "XKRX", "timezone": "Asia/Seoul"},
    "japan": {"mic": "XTKS", "timezone": "Asia/Tokyo"},
    "uk": {"mic": "XLON", "timezone": "Europe/London"},
    "hong_kong": {"mic": "XHKG", "timezone": "Asia/Hong_Kong"},
    "canada": {"mic": "XTSE", "timezone": "America/Toronto"},
    "germany": {"mic": "XETR", "timezone": "Europe/Berlin"},
    "china": {"mic": "XSHG", "timezone": "Asia/Shanghai"},
    "india": {"mic": "XBOM", "timezone": "Asia/Kolkata"},
    "taiwan": {"mic": "XTAI", "timezone": "Asia/Taipei"},
    "euronext": {"mic": "XPAR", "timezone": "Europe/Paris"},
}

# The upstream API accepts at most 366 days between `start` and `end`.
# Keep each request comfortably inside that limit and merge the results.
MAX_REQUEST_SPAN_DAYS = 365


def request_json(path: str, query: dict[str, str]) -> object:
    url = f"{API_BASE_URL}{path}?{urllib.parse.urlencode(query)}"
    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt < 3:
                time.sleep(attempt * 2)

    raise RuntimeError(f"Calendar request failed: {url}: {last_error}")


def request_holidays(mic: str, start: date, end: date) -> list[object]:
    """Fetch a long date range in API-safe chunks."""
    items: list[object] = []
    cursor = start

    while cursor <= end:
        chunk_end = min(
            cursor + timedelta(days=MAX_REQUEST_SPAN_DAYS),
            end,
        )
        raw = request_json(
            "/api/v1/markets/holidays",
            {
                "mic": mic,
                "start": cursor.isoformat(),
                "end": chunk_end.isoformat(),
            },
        )
        if not isinstance(raw, list):
            raise RuntimeError(
                f"Unexpected response for {mic} "
                f"({cursor.isoformat()} to {chunk_end.isoformat()}): {raw!r}"
            )
        items.extend(raw)
        cursor = chunk_end + timedelta(days=1)

    return items


def time_part(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        if "T" in text:
            return text.split("T", 1)[1][:5]
        return text[:5]


def normalize_holiday(item: dict[str, object]) -> dict[str, object] | None:
    if bool(item.get("is_weekend")):
        return None

    raw_date = str(item.get("date") or "").strip()
    if not raw_date:
        return None

    is_early_close = bool(item.get("is_early_close"))
    is_business_day = bool(item.get("is_business_day"))

    if is_business_day and not is_early_close:
        return None

    open_time = time_part(item.get("open_time"))
    close_time = time_part(item.get("close_time"))
    trading_hour = ""

    if is_early_close and open_time and close_time:
        trading_hour = f"{open_time}-{close_time}"

    holiday_name = str(item.get("holiday_name") or "").strip()
    if not holiday_name:
        holiday_name = "Early Close" if is_early_close else "Market Holiday"

    return {
        "atDate": raw_date,
        "eventName": holiday_name,
        "tradingHour": trading_hour,
        "isEarlyClose": is_early_close,
    }


def generate() -> dict[str, object]:
    today = date.today()
    start = today - timedelta(days=45)
    end = today + timedelta(days=730)
    markets: dict[str, object] = {}

    for market_id, definition in MARKETS.items():
        mic = definition["mic"]
        raw = request_holidays(mic, start, end)

        holidays_by_key: dict[tuple[str, str, bool], dict[str, object]] = {}
        for raw_item in raw:
            if not isinstance(raw_item, dict):
                continue
            normalized = normalize_holiday(raw_item)
            if normalized is not None:
                key = (
                    str(normalized["atDate"]),
                    str(normalized["eventName"]),
                    bool(normalized["isEarlyClose"]),
                )
                holidays_by_key[key] = normalized

        holidays = list(holidays_by_key.values())
        holidays.sort(key=lambda item: str(item["atDate"]))
        markets[market_id] = {
            "mic": mic,
            "timezone": definition["timezone"],
            "data": holidays,
        }
        print(f"{market_id:12} {mic}: {len(holidays)} events")

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "source": "apptastic-software/trading-calendar",
        "markets": markets,
    }


def main() -> int:
    payload = generate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
