"""News-blackout gate — refuses trades whose hold window overlaps a
high-impact economic event affecting the asset's currencies.

Window check: the trade is open from `now` to `now + trade_time` (binary
options have a fixed expiry, default 3600s). The gate blocks when any
event's [event_time - before, event_time + after] interval intersects
the trade-hold window. This catches the case where a trade is opened
shortly *before* the blackout window — naïve "is now inside the
blackout" checks let those slip through, only to have the release fire
mid-trade with chaotic price action. With trade_time omitted (or 0) the
gate falls back to the legacy point-in-time check.

Calendar source: Forex Factory's free weekly JSON feed
(`https://nfs.faireconomy.media/ff_calendar_thisweek.json`). The feed is
cached on disk at `data/economic_calendar.json` and refreshed when older
than `max_age_hours`. Network failures fall back to the cached file —
when the cache is also missing we err on the side of allowing the trade
(gate is opt-in).

Currency match: extracts every 3-letter ISO code from the asset name
(e.g. "AUDJPY" → {"AUD","JPY"}; "USDJPY_otc" → {"USD","JPY"}). An event
matches if its `country` (which Forex Factory encodes as the affected
currency) is in that set.
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.utils.gates.base import Gate, GateDecision


_IMPACT_RANK = {"low": 1, "medium": 2, "high": 3}
_ISO_RE = re.compile(r"[A-Z]{3}")


class NewsBlackoutGate(Gate):

    name = "news_blackout"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.before = int(config.get("blackout_minutes_before", 15))
        self.after = int(config.get("blackout_minutes_after", 15))
        self.min_impact = str(config.get("min_impact", "high")).lower()
        self.calendar_path = str(config.get("calendar_path", "data/economic_calendar.json"))
        self.calendar_url = str(config.get("calendar_url", ""))
        self.max_age_hours = float(config.get("max_age_hours", 24))
        self._events: Optional[List[Dict[str, Any]]] = None
        self._loaded_at: float = 0.0

    def _refresh_if_stale(self) -> None:
        # In-memory cache valid for the configured max_age_hours.
        if self._events is not None and (time.time() - self._loaded_at) < self.max_age_hours * 3600:
            return

        # On-disk file is the source of truth — try to refresh from net,
        # tolerate failure if a recent file already exists.
        needs_download = True
        if os.path.exists(self.calendar_path):
            age = time.time() - os.path.getmtime(self.calendar_path)
            if age < self.max_age_hours * 3600:
                needs_download = False

        if needs_download and self.calendar_url:
            try:
                # Forex Factory's faireconomy.media mirror returns 403 without
                # a User-Agent — present a generic UA to satisfy the filter.
                req = urllib.request.Request(
                    self.calendar_url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; hurz-news-blackout/1.0)"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8")
                os.makedirs(os.path.dirname(self.calendar_path) or ".", exist_ok=True)
                with open(self.calendar_path, "w", encoding="utf-8") as f:
                    f.write(raw)
            except Exception:
                # Fall through to the cached file, if any.
                pass

        self._events = self._read_calendar(self.calendar_path)
        self._loaded_at = time.time()

    @staticmethod
    def _read_calendar(path: str) -> List[Dict[str, Any]]:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("date") or entry.get("timestamp")
            country = entry.get("country") or entry.get("currency")
            impact = (entry.get("impact") or "").lower()
            title = entry.get("title") or entry.get("event") or ""
            try:
                # Forex Factory uses ISO 8601 with a trailing offset.
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            out.append({
                "dt": dt,
                "country": (country or "").upper(),
                "impact": impact,
                "title": title,
            })
        return out

    @staticmethod
    def _currencies_for(asset: str) -> set:
        # Strip OTC suffix and split out the two 3-letter ISO codes.
        core = asset.split("_")[0].upper()
        return set(_ISO_RE.findall(core))

    def _impact_passes(self, impact: str) -> bool:
        return _IMPACT_RANK.get(impact, 0) >= _IMPACT_RANK.get(self.min_impact, 3)

    def _now_utc(self, context: Dict[str, Any]) -> datetime:
        now = context.get("now") or datetime.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc).astimezone(timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        return now

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        self._refresh_if_stale()
        events = self._events or []
        if not events:
            return GateDecision(True, "news_blackout: no calendar available", {"events_loaded": 0})

        currencies = self._currencies_for(asset)
        if not currencies:
            return GateDecision(True, f"news_blackout: cannot parse currencies from {asset}")

        now = self._now_utc(context)
        trade_seconds = max(0, int(context.get("trade_time", 0) or 0))
        trade_end = now + timedelta(seconds=trade_seconds)
        window_before = timedelta(minutes=self.before)
        window_after = timedelta(minutes=self.after)

        for ev in events:
            if ev["country"] not in currencies:
                continue
            if not self._impact_passes(ev["impact"]):
                continue
            ev_dt = ev["dt"].astimezone(timezone.utc)
            blackout_start = ev_dt - window_before
            blackout_end = ev_dt + window_after
            # Interval overlap: [now, trade_end] ∩ [blackout_start, blackout_end]
            # Non-empty iff now <= blackout_end AND trade_end >= blackout_start.
            if now <= blackout_end and trade_end >= blackout_start:
                hold_min = trade_seconds // 60
                return GateDecision(
                    False,
                    (
                        f"news_blackout: {ev['country']} {ev['impact']} "
                        f"'{ev['title']}' at {ev_dt.isoformat()} overlaps "
                        f"trade window (hold={hold_min}min)"
                    ),
                    {
                        "event": ev["title"],
                        "country": ev["country"],
                        "impact": ev["impact"],
                        "event_time": ev_dt.isoformat(),
                        "trade_time_seconds": trade_seconds,
                    },
                )

        return GateDecision(True, f"news_blackout: clear ({len(currencies)} currencies × {len(events)} events)")
