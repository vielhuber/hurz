"""Generate a self-contained static HTML dashboard from the spot_trades
journal. No external assets — the chart is rendered as inline SVG so the
file opens offline via file:// or over http if /var/www/hurz is served.

Run once:   python3 scripts/generate_dashboard.py [days|all]
Automatic:  the bot's hourly heartbeat (autotrade.py) regenerates it,
            so it stays current whenever hurz runs. dashboard_loop.sh
            remains as an optional standalone fallback.

`days` defaults to "all" (the full span where data exists); pass an
integer to restrict to a rolling N-day window.

Output: dashboard.html in the repo root.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import mysql.connector
from dotenv import load_dotenv

# Same calendars as app/utils/holiday_window.py — the dashboard must agree
# with the bot's own guard about what counts as a bank holiday.
try:
    import holidays as _holidays

    _HOLIDAY_CALENDARS = {
        "DE": _holidays.country_holidays("DE"),
        "US": _holidays.country_holidays("US"),
        "GB": _holidays.country_holidays("GB"),
        "CH": _holidays.country_holidays("CH", subdiv="ZH"),
    }
except ImportError:
    _HOLIDAY_CALENDARS = {}

# All timestamps in the dashboard are shown in German local time
# (Europe/Berlin, DST-aware). DB and log timestamps are stored as UTC;
# _berlin() converts them for display. Falls back to UTC if the tz db
# is unavailable.
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Berlin")
except Exception:
    _TZ = timezone.utc


def _berlin(dt):
    """Convert a UTC (or naive-UTC) datetime to Berlin-local aware time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

_OUT_PATH = os.path.join(_ROOT, "dashboard.html")
# None = all-time (full span where data exists); int = rolling N-day window.
_DEFAULT_DAYS = None

# Per-platform line colour in the cumulative-PnL chart.
_COLORS = {
    "capital_com": "#2563eb",
    "kraken_futures": "#f59e0b",
    "__total__": "#16a34a",
}
_LABELS = {
    "capital_com": "Capital.com",
    "kraken_futures": "Kraken Futures",
    "__total__": "Total",
}


def _connect():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USERNAME", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "hurz"),
    )


def _rows(cur, query, params=None):
    cur.execute(query, params or ())
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# Retired strategies (mean-reversion, removed from the live rotation
# 2026-07-10). Excluded from the STAT views — PnL curve, per-platform
# summary/all-time, per-strategy & per-combo performance, projection — so
# the dashboard reflects the active (trend-only) book. The live "Offene
# Positionen" / "Letzte abgeschlossene Trades" tables stay unfiltered so
# the winding-down MR positions remain visible until they close.
_MR_EXCL = ("AND strategy NOT IN "
            "('bollinger_rev','stochastic_mr','rsi_mr')")


def _fetch(days) -> dict:
    conn = _connect()
    cur = conn.cursor()
    # All-time when days is None: drop the rolling-window clause entirely.
    win = "" if days is None else "AND exit_time >= NOW() - INTERVAL %s DAY"
    win_params = () if days is None else (days,)
    try:
        closed = _rows(cur, f"""
            SELECT platform, exit_time, realized_pnl
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL AND exit_time IS NOT NULL
              AND platform <> 'kraken_futures'
              {_MR_EXCL}
              {win}
            ORDER BY exit_time ASC
        """, win_params)
        summary = _rows(cur, f"""
            SELECT platform,
                   COUNT(*) AS trades,
                   SUM(realized_pnl > 0) AS wins,
                   SUM(realized_pnl <= 0) AS losses,
                   ROUND(SUM(realized_pnl), 2) AS pnl
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
              AND platform <> 'kraken_futures'
              {_MR_EXCL}
              {win}
            GROUP BY platform
        """, win_params)
        alltime = _rows(cur, f"""
            SELECT platform, ROUND(SUM(realized_pnl), 2) AS pnl
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
              AND platform <> 'kraken_futures'
              {_MR_EXCL}
            GROUP BY platform
        """)
        open_pos = _rows(cur, """
            SELECT platform, pair, strategy, direction, entry_price, created_at
            FROM spot_trades
            WHERE accepted=1 AND exit_time IS NULL AND deal_id IS NOT NULL
              AND platform <> 'kraken_futures'
            ORDER BY created_at ASC
        """)
        recent = _rows(cur, """
            SELECT id, platform, pair, strategy, direction,
                   entry_price, exit_price, outcome, realized_pnl, exit_time
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
              AND platform <> 'kraken_futures'
            ORDER BY exit_time DESC
            LIMIT 15
        """)
        # Forward (realized) performance per STRATEGY — which style class
        # actually earns live. This is the signal that counts (backtests
        # proved non-predictive).
        by_strategy = _rows(cur, f"""
            SELECT strategy,
                   COUNT(*) AS trades,
                   SUM(realized_pnl > 0) AS wins,
                   ROUND(SUM(realized_pnl), 2) AS pnl,
                   ROUND(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END)
                         / NULLIF(-SUM(CASE WHEN realized_pnl <= 0 THEN realized_pnl ELSE 0 END), 0), 2) AS pf,
                   ROUND(SUM(CASE WHEN exit_time >= NOW() - INTERVAL 30 DAY THEN realized_pnl ELSE 0 END), 2) AS pnl30,
                   ROUND(SUM(CASE WHEN exit_time >= NOW() - INTERVAL 14 DAY THEN realized_pnl ELSE 0 END), 2) AS pnl14,
                   ROUND(SUM(CASE WHEN exit_time >= NOW() - INTERVAL 7 DAY THEN realized_pnl ELSE 0 END), 2) AS pnl7
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
              AND platform <> 'kraken_futures'
              {_MR_EXCL}
            GROUP BY strategy
            ORDER BY pnl DESC
        """)
        # Forward performance per (pair, strategy) COMBO — the granular
        # view for filtering down to the profitable combos over time.
        by_combo = _rows(cur, f"""
            SELECT platform, pair, strategy,
                   COUNT(*) AS trades,
                   SUM(realized_pnl > 0) AS wins,
                   ROUND(SUM(realized_pnl), 2) AS pnl
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
              AND platform <> 'kraken_futures'
              {_MR_EXCL}
            GROUP BY platform, pair, strategy
            HAVING trades >= 2
            ORDER BY pnl DESC
        """)
        # Full-period span for the projection tile (how many days the
        # realized PnL was earned over — the daily-rate denominator).
        span = _rows(cur, f"""
            SELECT MIN(exit_time) AS mn, MAX(exit_time) AS mx, COUNT(*) AS n
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL AND exit_time IS NOT NULL
              AND platform <> 'kraken_futures'
              {_MR_EXCL}
        """)
        return {
            "closed": closed, "summary": summary, "alltime": alltime,
            "open": open_pos, "recent": recent,
            "by_strategy": by_strategy, "by_combo": by_combo,
            "span": span[0] if span else None,
        }
    finally:
        cur.close()
        conn.close()


def _cumulative_series(closed: list) -> list:
    """Build cumulative-PnL point lists keyed by platform plus a combined
    "__total__" series. Each point is (epoch_seconds, running_sum)."""
    per_platform: dict = {}
    running: dict = {}
    total_points = []
    total_run = 0.0
    for row in closed:
        plat = row["platform"]
        ts = row["exit_time"]
        epoch = ts.replace(tzinfo=timezone.utc).timestamp() if ts.tzinfo is None \
            else ts.timestamp()
        pnl = float(row["realized_pnl"] or 0.0)
        running[plat] = running.get(plat, 0.0) + pnl
        per_platform.setdefault(plat, []).append((epoch, running[plat]))
        total_run += pnl
        total_points.append((epoch, total_run))
    series = []
    for plat, points in per_platform.items():
        series.append({"key": plat, "points": points})
    if len(per_platform) > 1:
        series.append({"key": "__total__", "points": total_points})
    return series


def _period_label(days, closed: list) -> str:
    """Human label for the covered period. All-time → derive the real
    span from the data; windowed → 'letzte N Tage'."""
    if days is not None:
        return f"letzte {days} Tage"
    if not closed:
        return "gesamter Zeitraum"
    first = _berlin(closed[0]["exit_time"])
    last = _berlin(closed[-1]["exit_time"])
    span = (last - first).days
    return f"seit {first:%d.%m.%Y} ({span} Tage)"


def _fmt_money(value) -> str:
    v = float(value or 0.0)
    sign = "+" if v >= 0 else "−"
    return f"{sign}${abs(v):,.2f}"


def _money_class(value) -> str:
    return "pos" if float(value or 0.0) >= 0 else "neg"


def _render_chart(series: list, width: int = 920, height: int = 340) -> str:
    pts_all = [p for s in series for p in s["points"]]
    if not pts_all:
        return ('<div class="nodata">Keine abgeschlossenen Trades im '
                'gewählten Zeitraum.</div>')
    ml, mr, mt, mb = 56, 16, 28, 28
    pw, ph = width - ml - mr, height - mt - mb
    xs = [p[0] for p in pts_all]
    ys = [p[1] for p in pts_all]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys + [0.0]), max(ys + [0.0])
    if xmax == xmin:
        xmax = xmin + 1
    yrange = (ymax - ymin) or 1.0
    ymin -= yrange * 0.08
    ymax += yrange * 0.08

    def px(x):
        return ml + (x - xmin) / (xmax - xmin) * pw

    def py(y):
        return mt + (ymax - y) / (ymax - ymin) * ph

    parts = [f'<svg viewBox="0 0 {width} {height}" '
             f'preserveAspectRatio="xMidYMid meet" class="chart">']
    # horizontal gridlines + y labels
    for i in range(5):
        yv = ymin + (ymax - ymin) * i / 4
        yy = py(yv)
        parts.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{width-mr}" '
                     f'y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{ml-8}" y="{yy+4:.1f}" class="ylab" '
                     f'text-anchor="end">{_fmt_money(yv)}</text>')
    # zero line emphasis
    if ymin < 0 < ymax:
        zy = py(0.0)
        parts.append(f'<line x1="{ml}" y1="{zy:.1f}" x2="{width-mr}" '
                     f'y2="{zy:.1f}" class="zero"/>')
    # x date ticks
    for i in range(5):
        xv = xmin + (xmax - xmin) * i / 4
        xx = px(xv)
        label = _berlin(datetime.fromtimestamp(xv, tz=timezone.utc)).strftime("%d.%m.")
        parts.append(f'<text x="{xx:.1f}" y="{height-8}" class="xlab" '
                     f'text-anchor="middle">{label}</text>')
    # series polylines
    for s in series:
        color = _COLORS.get(s["key"], "#888")
        pts = s["points"]
        coords = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)
        if len(pts) == 1:
            x, y = pts[0]
            parts.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3" '
                         f'fill="{color}"/>')
        else:
            parts.append(f'<polyline points="{coords}" fill="none" '
                         f'stroke="{color}" stroke-width="2"/>')
    parts.append("</svg>")
    # legend
    leg = ['<div class="legend">']
    for s in series:
        color = _COLORS.get(s["key"], "#888")
        label = _LABELS.get(s["key"], s["key"])
        leg.append(f'<span class="lg"><i style="background:{color}"></i>'
                   f'{label}</span>')
    leg.append("</div>")
    return "".join(parts) + "".join(leg)


def _render_cards(summary: list, alltime: list, open_pos: list,
                  period: str) -> str:
    by_plat = {r["platform"]: r for r in summary}
    alltime_by = {r["platform"]: float(r["pnl"] or 0.0) for r in alltime}
    open_count: dict = {}
    for p in open_pos:
        open_count[p["platform"]] = open_count.get(p["platform"], 0) + 1
    cards = []
    for plat in ("capital_com",):  # Kraken retired 2026-07-02
        s = by_plat.get(plat, {})
        trades = int(s.get("trades") or 0)
        wins = int(s.get("wins") or 0)
        pnl = float(s.get("pnl") or 0.0)
        wr = (wins / trades * 100) if trades else 0.0
        at = alltime_by.get(plat, 0.0)
        cards.append(f"""
        <div class="card">
          <h2>{_LABELS[plat]}</h2>
          <div class="big {_money_class(pnl)}">{_fmt_money(pnl)}</div>
          <div class="sub">realisiert · {period}</div>
          <div class="row"><span>Trades</span><b>{trades}</b></div>
          <div class="row"><span>Win-Rate</span><b>{wr:.0f}%</b></div>
          <div class="row"><span>Offen</span><b>{open_count.get(plat, 0)}</b></div>
          <div class="row"><span>All-time</span>
            <b class="{_money_class(at)}">{_fmt_money(at)}</b></div>
        </div>""")
    return "".join(cards)


# Max holding time before the autotrader's stale-exit force-closes a
# position = HURZ_MAX_HOLD_BARS (24) × bar_seconds(resolution). Capital
# runs 1h bars (→24h), Kraken futures 4h bars (→96h). This is the only
# DETERMINISTIC "time left" — a trade may close earlier on TP/SL, but it
# will close no later than this. Purely from created_at, no live price.
_STALE_HOLD_HOURS = {"capital_com": 24.0, "kraken_futures": 96.0}


def _fmt_duration(minutes: float) -> str:
    """Human duration from minutes → 'Xd Yh Zm' (dropping zero units).
    e.g. 193 → '3h 13m', 5760 → '4d', 45 → '45m'."""
    m = int(round(minutes))
    d, rem = divmod(m, 1440)
    h, mm = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if mm or not parts:
        parts.append(f"{mm}m")
    return " ".join(parts)


def _render_open(open_pos: list) -> str:
    if not open_pos:
        return '<tr><td colspan="6" class="muted">Keine offenen Positionen.</td></tr>'
    now = datetime.now(timezone.utc)
    # Sort so the position closest to its forced close (smallest rest) is on top.
    def rest_min(p):
        ct = p["created_at"]
        ct = ct.replace(tzinfo=timezone.utc) if ct.tzinfo is None else ct
        hold_h = _STALE_HOLD_HOURS.get(p["platform"], 24.0)
        deadline = ct + timedelta(hours=hold_h)
        return (deadline - now).total_seconds() / 60.0
    out = []
    for p in sorted(open_pos, key=rest_min):
        ct = p["created_at"]
        ct = ct.replace(tzinfo=timezone.utc) if ct.tzinfo is None else ct
        age_h = (now - ct).total_seconds() / 3600
        d = "Long" if int(p["direction"]) == 1 else "Short"  # neutral colour: dir ≠ profit
        rm = rest_min(p)
        if rm <= 0:
            rest_cell = '<span class="neg">überfällig</span>'
        else:
            rest_cell = _fmt_duration(rm)
        out.append(f"""<tr>
          <td>{_LABELS.get(p['platform'], p['platform'])}</td>
          <td>{p['pair']}</td>
          <td>{p['strategy']}</td>
          <td>{d}</td>
          <td>{age_h:.1f}h</td>
          <td><b>{rest_cell}</b></td></tr>""")
    return "".join(out)


def _render_recent(recent: list) -> str:
    if not recent:
        return '<tr><td colspan="6" class="muted">Noch keine Trades.</td></tr>'
    out = []
    for r in recent:
        d = "Long" if int(r["direction"]) == 1 else "Short"
        when = _berlin(r["exit_time"]).strftime("%d.%m. %H:%M") if r["exit_time"] else "—"
        out.append(f"""<tr>
          <td>{when}</td>
          <td>{_LABELS.get(r['platform'], r['platform'])}</td>
          <td>{r['pair']}</td>
          <td>{r['strategy']}</td>
          <td>{r['outcome']}</td>
          <td class="{_money_class(r['realized_pnl'])}">{_fmt_money(r['realized_pnl'])}</td>
        </tr>""")
    return "".join(out)


# (label, pid_file, log_file) for each bot session shown in the status
# panel. Kraken Futures removed 2026-07-02: its demo API was dead for ~13
# days (503 on every endpoint, Kraken's side) and it never recovered —
# we retired it. Capital.com is the only live platform. Historical Kraken
# trades stay in the record; it just no longer shows as an active bot.
# Re-add this line if Kraken's demo ever comes back.
_BOTS = [
    ("Capital.com", "tmp/paper_session.pid", "tmp/paper_session.log"),
]

_RE_HEARTBEAT = re.compile(
    r"\[spot\]\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+heartbeat:.*?(\d+)\s+open")
_RE_TS = re.compile(r"\[spot\]\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
_RE_NOTIONAL = re.compile(r"notional_per_trade=\$?([\d.]+)")

# A bot may keep logging (e.g. Kraken's 503 retries) without heartbeating.
# If the latest log activity is within this window the process is alive
# and working, just API-degraded — not "stale".
_ACTIVITY_FRESH_MIN = 15
# Heartbeat older than this = not cycling normally.
_HEARTBEAT_STALE_MIN = 90
_RE_REGIME = re.compile(r"regime_filter=(.+?)\s*$")


def _pid_alive(pid_file: str) -> tuple:
    """Return (alive, pid) by checking the PID file against the OS."""
    try:
        with open(os.path.join(_ROOT, pid_file), encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False, None
    try:
        os.kill(pid, 0)
        return True, pid
    except OSError:
        return False, pid


def _bot_status(label: str, pid_file: str, log_file: str) -> dict:
    """Probe one bot: PID liveness + parse the tail of its log for the
    last heartbeat, open positions, notional, regime-filter state and a
    bank-holiday marker. Pure filesystem/process — no DB."""
    alive, pid = _pid_alive(pid_file)
    open_pos = notional = regime = None
    last_hb_dt = None
    try:
        with open(os.path.join(_ROOT, log_file), encoding="utf-8",
                  errors="replace") as f:
            lines = f.read().replace("\x00", "").splitlines()[-3000:]
    except OSError:
        lines = []
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    last_any_dt = None
    # Two clocks, both as MAX timestamp (order-independent — overlapping
    # writers can leave log lines out of order):
    #   last_hb_dt  — latest HEARTBEAT: is the bot cycling/trading normally
    #   last_any_dt — latest ANY log line: is the process alive and doing
    #                 something (a 503-backoff still logs, just no heartbeat)
    for line in lines:
        m = _RE_HEARTBEAT.search(line)
        if m:
            try:
                dt = datetime.strptime(
                    m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                dt = None
            if dt is not None and (last_hb_dt is None or dt > last_hb_dt):
                last_hb_dt = dt
                open_pos = int(m.group(2))
        m = _RE_TS.search(line)
        if m:
            try:
                dt = datetime.strptime(
                    m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                dt = None
            if dt is not None and (last_any_dt is None or dt > last_any_dt):
                last_any_dt = dt
        m = _RE_NOTIONAL.search(line)
        if m:
            notional = m.group(1)
        m = _RE_REGIME.search(line)
        if m:
            regime = m.group(1)
    # Ask the holiday calendars directly (same source as the bot's guard).
    # Grepping the log for "BANK HOLIDAY <today>" misses multi-day spans:
    # the bot prints that marker only once when entering its idle loop, so
    # on e.g. observed Fri + actual Sat the line carries yesterday's date.
    local_today = datetime.now().date()
    holiday_name = next((name for cal in _HOLIDAY_CALENDARS.values()
                         if (name := cal.get(local_today))), None)
    holiday = holiday_name is not None
    if not holiday:
        # Fallback when the holidays lib is unavailable: log marker for today.
        holiday_line = next((ln for ln in reversed(lines)
                             if f"BANK HOLIDAY {today}" in ln), None)
        holiday = holiday_line is not None
        if holiday_line is not None:
            hm = re.search(
                rf"BANK HOLIDAY {re.escape(today)}\s*[—–-]+\s*(.+?)\s*[—–-]+",
                holiday_line)
            holiday_name = hm.group(1).strip() if hm else None
    hb_age = ((now - last_hb_dt).total_seconds() / 60
              if last_hb_dt is not None else None)
    act_age = ((now - last_any_dt).total_seconds() / 60
               if last_any_dt is not None else None)
    last_hb = (_berlin(last_hb_dt).strftime("%d.%m.%Y %H:%M:%S")
               if last_hb_dt is not None else None)
    last_act = (_berlin(last_any_dt).strftime("%d.%m.%Y %H:%M:%S")
                if last_any_dt is not None else None)

    # Derive a state + colour. Three honest levels:
    #   green  — heartbeating normally
    #   amber  — alive but degraded (API-backoff: logging, no heartbeat)
    #   gray   — paused for a holiday
    #   red    — process is gone
    if not alive:
        state, tone = "gestoppt", "down"
    elif hb_age is not None and hb_age <= _HEARTBEAT_STALE_MIN:
        state, tone = "läuft", "ok"
    elif holiday:
        state, tone = "pausiert (Feiertag)", "idle"
    elif act_age is not None and act_age <= _ACTIVITY_FRESH_MIN:
        # Process is alive and logging (typically an API-error backoff)
        # but hasn't completed a heartbeat cycle.
        state, tone = "läuft (API-Backoff)", "warn"
    elif hb_age is None and act_age is None:
        state, tone = "startet…", "warn"
    else:
        idle = act_age if act_age is not None else hb_age
        state, tone = f"inaktiv (still seit {idle:.0f} min)", "warn"
    return {
        "label": label, "alive": alive, "pid": pid, "state": state,
        "tone": tone, "last_hb": last_hb, "last_act": last_act,
        "open_pos": open_pos, "notional": notional, "regime": regime,
        "age_min": hb_age, "holiday": holiday, "holiday_name": holiday_name,
        "holiday_date": _berlin(now).strftime("%d.%m.%Y"),
    }


def _render_status(stats: dict) -> str:
    rows = []
    for label, pid_file, log_file in _BOTS:
        s = _bot_status(label, pid_file, log_file)
        hb = s["last_hb"] or "—"
        if s["age_min"] is not None and s["last_hb"]:
            hb += f' <span class="dim">(vor {s["age_min"]:.0f} min)</span>'
        act = s.get("last_act") or "—"
        op = "—" if s["open_pos"] is None else s["open_pos"]
        notional = f'${s["notional"]}' if s["notional"] else "—"
        regime = s["regime"] or "—"
        hol = ""
        if s.get("holiday"):
            name = s.get("holiday_name") or "Feiertag"
            hol = (f'<div class="holnote">🎌 Feiertag {s.get("holiday_date", "")} — '
                   f'{name}: Börse geschlossen, Handel ausgesetzt · nimmt am nächsten '
                   f'Handelstag automatisch wieder auf (offene Positionen bleiben offen).</div>')
        if not hol:
            # Weekend note: the bot keeps running (crypto trades 24/7), but
            # FX/index markets are closed — pending closes queue until open.
            now_berlin = _berlin(datetime.now(timezone.utc))
            if (now_berlin.weekday() == 5
                    or (now_berlin.weekday() == 6 and now_berlin.hour < 23)):
                hol = ('<div class="holnote">🛌 Wochenende — Devisen- und Index'
                       'märkte geschlossen, Krypto handelt weiter · fällige '
                       'Schließungen werden nach Marktöffnung (FX So 23:00 Uhr, '
                       'Indizes Mo früh) automatisch nachgeholt.</div>')
        rows.append(f"""
        <div class="statusrow">
          <div class="stitle"><span class="dot {s['tone']}"></span>
            <b>{s['label']}</b> <span class="state {s['tone']}">{s['state']}</span></div>
          <div class="smeta">
            <span>Letzter Heartbeat: {hb}</span>
            <span>Letzte Aktivität: {act}</span>
            <span>Offen: {op}</span>
            <span>Notional: {notional}</span>
            <span>Regime-Filter: {regime}</span>
          </div>{hol}
        </div>""")
    # Compact live-activity summary — also fills the tile so it matches the
    # height of the Capital card next to it (no big empty area).
    t_n = stats.get("today_n", 0)
    t_win = stats.get("today_win", 0)
    t_wr = (t_win / t_n * 100) if t_n else 0.0
    t_pnl = stats.get("today_pnl", 0.0)
    at = stats.get("alltime", 0.0)
    # 3 rows keeps the tile ≈ the height of the Capital card next to it
    # (5 rows made it taller → empty space in the card). Win% is merged
    # into the Trades row.
    rows.append(f"""
        <div class="statrows">
          <div class="row"><span>Trades heute</span><b>{t_n} ({t_wr:.0f}% Gewinn)</b></div>
          <div class="row"><span>PnL heute</span>
            <b class="{_money_class(t_pnl)}">{_fmt_money(t_pnl)}</b></div>
          <div class="row"><span>All-time</span>
            <b class="{_money_class(at)}">{_fmt_money(at)}</b></div>
        </div>""")
    return "".join(rows)


def _render_strategy_perf(by_strategy: list, by_combo: list) -> str:
    """Forward-performance tables: per strategy, and top/bottom combos.
    This is the 'filter the winners' view — driven by realized live
    results, not backtests."""
    def wr(row):
        t = int(row["trades"] or 0)
        return (int(row["wins"] or 0) / t * 100) if t else 0.0

    strat_rows = []
    if not by_strategy:
        strat_rows.append('<tr><td colspan="8" class="muted">Noch keine realisierten Trades.</td></tr>')
    for r in by_strategy:
        pf = r.get("pf")
        pf_s = "∞" if pf is None else f"{float(pf):.2f}"
        strat_rows.append(f"""<tr>
          <td>{r['strategy']}</td>
          <td>{int(r['trades'] or 0)}</td>
          <td>{wr(r):.0f}%</td>
          <td>{pf_s}</td>
          <td class="{_money_class(r['pnl'])}">{_fmt_money(r['pnl'])}</td>
          <td class="{_money_class(r.get('pnl30'))}">{_fmt_money(r.get('pnl30'))}</td>
          <td class="{_money_class(r.get('pnl14'))}">{_fmt_money(r.get('pnl14'))}</td>
          <td class="{_money_class(r.get('pnl7'))}">{_fmt_money(r.get('pnl7'))}</td></tr>""")

    # Top 8 and bottom 5 combos by PnL.
    combo_rows = []
    if not by_combo:
        combo_rows.append('<tr><td colspan="5" class="muted">Noch keine Combos mit ≥2 Trades.</td></tr>')
    else:
        shown = by_combo[:8]
        if len(by_combo) > 12:
            shown = by_combo[:8] + by_combo[-4:]
        for r in shown:
            combo_rows.append(f"""<tr>
              <td>{_LABELS.get(r['platform'], r['platform'])}</td>
              <td>{r['pair']}</td>
              <td>{r['strategy']}</td>
              <td>{int(r['trades'] or 0)} · {wr(r):.0f}%</td>
              <td class="{_money_class(r['pnl'])}">{_fmt_money(r['pnl'])}</td></tr>""")

    return f"""
  <div class="panel">
    <h3>Strategie-Performance (realisiert, live) — was zählt</h3>
    <div style="overflow-x:auto;"><table>
      <thead><tr><th>Strategie</th><th>Trades</th><th>Win%</th><th>PF</th>
        <th>PnL ges.</th><th>30 T</th><th>14 T</th><th>7 T</th></tr></thead>
      <tbody>{''.join(strat_rows)}</tbody>
    </table></div>
  </div>
  <div class="panel">
    <h3>Beste / schlechteste Kombinationen (Pair × Strategie, ≥2 Trades)</h3>
    <table>
      <thead><tr><th>Platform</th><th>Pair</th><th>Strategie</th>
        <th>Trades · Win%</th><th>PnL</th></tr></thead>
      <tbody>{''.join(combo_rows)}</tbody>
    </table>
  </div>"""


# --- Projected-gains tile -------------------------------------------------
# "If this were a live account, with a reasonable stake, running only the
# top-3 performer combos — how many EUR/day net?" Computed live from the
# realized (demo-fill) results, so it self-updates. Assumptions surfaced in
# the tile's caption. Costs are already inside the realized fills (spread);
# overnight CFD financing is NOT, hence the conservative haircut.
_PROJ_STAKE_EUR = 1000      # "reasonable" stake per trade
_PROJ_EUR_USD = 1.087       # EUR stake -> USD notional
_PROJ_USD_EUR = 0.92        # USD PnL -> EUR
_PROJ_BASE_NOTIONAL = 250   # current demo notional/trade (HURZ_NOTIONAL_PER_TRADE)
_PROJ_HAIRCUT = 0.5         # conservative discount: small samples + live costs


def _render_projection(by_combo: list, span, by_strategy: list) -> str:
    # Only project the WINNERS: combos from strategy classes that are net
    # positive over their whole live history. This drops lucky pairs of
    # net-losing strategies (e.g. a single good EURUSD run inside the
    # −$108 stochastic_mr book) — analysis 2026-07-10 showed mean-reversion
    # loses in every regime, so it is not projectable edge.
    winner_strats = {r["strategy"] for r in (by_strategy or [])
                     if float(r.get("pnl") or 0) > 0}
    top3 = [r for r in (by_combo or [])
            if r["strategy"] in winner_strats and float(r.get("pnl") or 0) > 0][:3]
    if not top3 or not span or not span.get("mn") or not span.get("mx"):
        return """
  <div class="panel proj">
    <h3>Projected Gains</h3>
    <div class="muted">Noch nicht genug profitable Daten für eine Projektion.</div>
  </div>"""
    period_days = max(1, (span["mx"] - span["mn"]).days)
    total = sum(float(r["pnl"] or 0) for r in top3)
    scale = (_PROJ_STAKE_EUR * _PROJ_EUR_USD / _PROJ_BASE_NOTIONAL) * _PROJ_USD_EUR

    def eur_day(pnl_usd: float) -> float:
        return (pnl_usd / period_days) * scale

    opt = eur_day(total)
    cons = opt * _PROJ_HAIRCUT

    rows = []
    for r in top3:
        t = int(r["trades"] or 0)
        wr = (int(r["wins"] or 0) / t * 100) if t else 0.0
        warn = ' <span class="pwarn">⚠</span>' if t < 5 else ""
        rows.append(f"""<tr>
          <td>{r['strategy']} <span class="dim">{r['pair']}</span>{warn}</td>
          <td>{t} · {wr:.0f}%</td>
          <td class="pos">{_fmt_money(r['pnl'])}</td>
          <td class="pos">€{eur_day(float(r['pnl'])):.2f}</td></tr>""")

    # Compact band chart: net EUR/day vs stake (linear through ~origin).
    W, H, ml, mr, mt, mb = 470, 196, 42, 12, 14, 30
    x0, x1, yb, yt = ml, W - mr, H - mb, mt
    smin, smax = 250, 5000
    ymax = max(10.0, opt * (smax / _PROJ_STAKE_EUR))
    # round ymax up to a clean step
    step = 10 if ymax <= 50 else 20
    ymax = (int(ymax / step) + 1) * step

    def sx(s):
        return x0 + (s - smin) / (smax - smin) * (x1 - x0)

    def sy(v):
        return yb - v / ymax * (yb - yt)

    def dv(s):
        return opt * (s / _PROJ_STAKE_EUR)

    svg = [f'<svg viewBox="0 0 {W} {H}" class="pchart" role="img" '
           f'aria-label="Projiziertes Netto pro Tag nach Einsatz">']
    for gv in range(0, int(ymax) + 1, step):
        svg.append(f'<line class="pgrid" x1="{x0}" y1="{sy(gv):.1f}" '
                   f'x2="{x1}" y2="{sy(gv):.1f}"/>')
        svg.append(f'<text class="pax" x="{x0-8}" y="{sy(gv)+3:.1f}" '
                   f'text-anchor="end">€{gv}</text>')
    for sv in (250, 1000, 2500, 5000):
        lab = f"€{sv//1000}k" if sv >= 1000 else f"€{sv}"
        svg.append(f'<text class="pax" x="{sx(sv):.1f}" y="{yb+18}" '
                   f'text-anchor="middle">{lab}</text>')
    # band (cons .. opt)
    band = (f'M{sx(smin):.1f},{sy(dv(smin)):.1f} L{sx(smax):.1f},{sy(dv(smax)):.1f} '
            f'L{sx(smax):.1f},{sy(dv(smax)*_PROJ_HAIRCUT):.1f} '
            f'L{sx(smin):.1f},{sy(dv(smin)*_PROJ_HAIRCUT):.1f} Z')
    svg.append(f'<path d="{band}" class="pband"/>')
    svg.append(f'<line class="pcons" x1="{sx(smin):.1f}" y1="{sy(dv(smin)*_PROJ_HAIRCUT):.1f}" '
               f'x2="{sx(smax):.1f}" y2="{sy(dv(smax)*_PROJ_HAIRCUT):.1f}"/>')
    svg.append(f'<line class="popt" x1="{sx(smin):.1f}" y1="{sy(dv(smin)):.1f}" '
               f'x2="{sx(smax):.1f}" y2="{sy(dv(smax)):.1f}"/>')
    # highlight the reasonable stake
    hx = sx(_PROJ_STAKE_EUR)
    svg.append(f'<line class="phair" x1="{hx:.1f}" y1="{sy(0):.1f}" x2="{hx:.1f}" y2="{sy(opt):.1f}"/>')
    svg.append(f'<circle cx="{hx:.1f}" cy="{sy(opt):.1f}" r="4" class="pdot-o"/>')
    svg.append(f'<circle cx="{hx:.1f}" cy="{sy(cons):.1f}" r="4" class="pdot-c"/>')
    svg.append('</svg>')
    chart = "".join(svg)

    return f"""
  <div class="panel proj">
    <h3>Projected Gains <span class="dim">— hypothetisches Live-Konto</span></h3>
    <div class="proj-grid">
      <div class="proj-left">
        <div class="proj-hero">
          <div class="proj-num">€{cons:.0f}–{opt:.0f}<span class="proj-unit">/ Tag</span></div>
          <div class="proj-sub">netto · Top-3 Trend-Kombos · €{_PROJ_STAKE_EUR:,}/Trade ·
            ≈ €{cons*30:.0f}–{opt*30:.0f}/Monat</div>
        </div>
        <div style="overflow-x:auto;"><table class="proj-table">
          <thead><tr><th>Strategie · Pair</th><th>Trades</th>
            <th>Netto {period_days}T</th><th>€/Tag</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>
        <div class="proj-note">Basis: nur profitable Strategie-Klassen (Mean-Reversion
          verliert in jedem Regime → ausgeschlossen). Kleine Stichproben (⚠),
          Overnight-Kosten &amp; Slippage nicht enthalten, Vergangenheit ≠ Zukunft.
          Keine Anlageberatung.</div>
      </div>
      <div class="proj-right">
        <div class="proj-clabel"><span><i class="li-o"></i>optimistisch</span>
          <span><i class="li-c"></i>konservativ</span></div>
        {chart}
      </div>
    </div>
  </div>"""


def _render_html(data: dict, days) -> str:
    period = _period_label(days, data["closed"])
    series = _cumulative_series(data["closed"])
    chart = _render_chart(series)
    # Live-activity stats for the Bot-Status tile. "Today" is a Berlin-day
    # filter over the closed trades (tz-correct); signals/vetos come from
    # the Capital session log (best-effort).
    now_b = _berlin(datetime.now(timezone.utc))
    today_d = now_b.date()
    tc = [c for c in data["closed"]
          if _berlin(c["exit_time"]) and _berlin(c["exit_time"]).date() == today_d]
    sig24 = veto24 = None
    try:
        with open(os.path.join(_ROOT, "tmp/paper_session.log"),
                  encoding="utf-8", errors="replace") as f:
            _txt = f.read().replace("\x00", "")
        _hbs = re.findall(r"heartbeat: scanned \d+ pairs,[^,]*,\s*(\d+) signals", _txt)
        if _hbs:
            sig24 = int(_hbs[-1])
        veto24 = _txt.count("regime-veto")
    except OSError:
        pass
    stats = {
        "today_n": len(tc),
        "today_win": sum(1 for c in tc if float(c["realized_pnl"] or 0) > 0),
        "today_pnl": sum(float(c["realized_pnl"] or 0) for c in tc),
        "alltime": sum(float(r["pnl"] or 0) for r in data["alltime"]),
        "signals_24h": sig24,
        "vetoes_24h": veto24,
    }
    status = _render_status(stats)
    cards = _render_cards(data["summary"], data["alltime"], data["open"], period)
    open_rows = _render_open(data["open"])
    recent_rows = _render_recent(data["recent"])
    strategy_perf = _render_strategy_perf(
        data.get("by_strategy", []), data.get("by_combo", []))
    projection = _render_projection(
        data.get("by_combo", []), data.get("span"), data.get("by_strategy", []))
    # Marker for the browser sound-on-close: latest closed trade's id + win.
    _recent = data.get("recent") or []
    latest_id = str(_recent[0]["id"]) if _recent else ""
    latest_win = "1" if (_recent and float(_recent[0]["realized_pnl"] or 0) > 0) else "0"
    generated = _berlin(datetime.now(timezone.utc)).strftime("%d.%m.%Y %H:%M:%S %Z")
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hurz Trading Dashboard</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: #0e1116; color: #e6e8eb; }}
  .wrap {{ max-width: none; margin: 0; padding: 24px 22px 48px; }}
  /* Main content split: left = chart + strategy panels, right = open
     positions + recent trades (the live activity), each ~50%. */
  .maingrid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
               align-items: start; }}
  .col > .panel:last-child {{ margin-bottom: 0; }}
  header {{ display: flex; align-items: baseline; justify-content: space-between;
            flex-wrap: wrap; gap: 8px; margin-bottom: 18px;
            padding-right: 130px; }}  /* clear the fixed sound button top-right */
  h1 {{ font-size: 20px; margin: 0; color: #f3f4f6; }}
  .updated {{ color: #8b91a0; font-size: 13px; }}
  .updated b {{ color: #cfd3db; }}
  /* Top row: Bot-Status and the Capital card side by side. CSS Grid with
     two `1fr` tracks guarantees mathematically equal column widths (flex
     `1 1 0` measurably did NOT here — Chrome gave 687 vs 649px because the
     padding-less .cards wrapper and the padded .panel size differently),
     and `align-items: stretch` equalizes their heights for free — so the
     old equalTop() JS hack is gone. */
  .toprow {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
             align-items: stretch; margin-bottom: 20px; }}
  .toprow > * {{ min-width: 0; box-sizing: border-box; }}
  /* Higher specificity so it beats the later `.panel {{ margin-bottom:20px }}`
     (equal specificity → source order would otherwise win). The toprow
     already provides the gap below the row; its children must NOT add
     their own bottom margin — that was the extra-gap-on-the-left bug. */
  .toprow > .panel, .toprow > .cards {{ margin-bottom: 0; }}
  /* flex column so the card FILLS the stretched cell height (a grid cell
     would leave the card at content-height, shorter than the status panel). */
  .cards {{ display: flex; flex-direction: column; gap: 20px; }}
  .cards > .card {{ flex: 1; }}
  .card {{ background: #171a21; border: 1px solid #262b36; border-radius: 12px;
           padding: 16px 18px; }}
  .card h2 {{ font-size: 14px; margin: 0 0 6px; color: #aab0bd;
              font-weight: 600; }}
  .big {{ font-size: 28px; font-weight: 700; }}
  .sub {{ color: #6b7280; font-size: 12px; margin-bottom: 10px; }}
  .row {{ display: flex; justify-content: space-between; font-size: 13px;
          padding: 3px 0; border-top: 1px solid #232833; }}
  .panel {{ background: #171a21; border: 1px solid #262b36; border-radius: 12px;
            padding: 16px 18px; margin-bottom: 20px; }}
  .panel h3 {{ font-size: 14px; margin: 0 0 12px; color: #aab0bd; }}
  .chart {{ width: 100%; height: auto; }}
  .grid {{ stroke: #232833; stroke-width: 1; }}
  .zero {{ stroke: #4b5563; stroke-width: 1; stroke-dasharray: 3 3; }}
  .ylab, .xlab {{ fill: #8b91a0; font-size: 11px; }}
  .legend {{ display: flex; gap: 16px; margin-top: 8px; padding-left: 56px; }}
  .lg {{ font-size: 12px; color: #aab0bd; display: flex; align-items: center;
         gap: 5px; }}
  .lg i {{ width: 11px; height: 11px; border-radius: 2px; display: inline-block; }}
  .nodata {{ color: #8b91a0; padding: 40px; text-align: center; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid #232833; }}
  th {{ color: #8b91a0; font-weight: 600; font-size: 12px; }}
  .pos {{ color: #34d399; }}
  .neg {{ color: #f87171; }}
  .status .statusrow {{ padding: 8px 0; border-top: 1px solid #232833; }}
  .status .statusrow:first-of-type {{ border-top: none; }}
  .statrows {{ margin-top: 10px; }}
  .holnote {{ margin-top: 8px; padding: 7px 11px; border-radius: 7px;
              background: #2a2410; border: 1px solid #6b5a12;
              color: #fbbf24; font-size: 12.5px; line-height: 1.45; }}
  .stitle {{ display: flex; align-items: center; gap: 8px; font-size: 14px; }}
  .smeta {{ display: flex; flex-wrap: wrap; gap: 14px; margin-top: 4px;
            padding-left: 18px; color: #8b91a0; font-size: 12px; }}
  .dim {{ color: #6b7280; }}
  /* Projected-gains tile */
  .proj-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; align-items: center; }}
  .proj-hero {{ margin-bottom: 12px; }}
  .proj-num {{ font-size: 34px; font-weight: 700; color: #34d399; letter-spacing: -.02em;
               font-variant-numeric: tabular-nums; line-height: 1; }}
  .proj-num .proj-unit {{ font-size: 14px; color: #8b91a0; font-weight: 500; margin-left: 8px; }}
  .proj-sub {{ color: #aab0bd; font-size: 12.5px; margin-top: 7px; }}
  .proj-table {{ margin-top: 4px; }}
  .proj-table th, .proj-table td {{ padding: 6px 8px; font-variant-numeric: tabular-nums; }}
  .proj-table td:nth-child(n+2), .proj-table th:nth-child(n+2) {{ text-align: right; }}
  .pwarn {{ color: #d9a441; }}
  .proj-note {{ margin-top: 10px; color: #7e8794; font-size: 11.5px; line-height: 1.5; }}
  .proj-right {{ min-width: 0; }}
  .proj-clabel {{ display: flex; gap: 16px; justify-content: flex-end; font-size: 12px;
                  color: #aab0bd; margin-bottom: 2px; }}
  .proj-clabel span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .proj-clabel i {{ width: 12px; height: 3px; border-radius: 2px; display: inline-block; }}
  .li-o {{ background: #34d399; }}
  .li-c {{ background: #d9a441; }}
  .pchart {{ width: 100%; height: auto; display: block; }}
  .pgrid {{ stroke: #232833; stroke-width: 1; }}
  .pax {{ fill: #8b91a0; font-size: 10px; }}
  .pband {{ fill: rgba(52,211,153,.13); }}
  .popt {{ stroke: #34d399; stroke-width: 2.5; }}
  .pcons {{ stroke: #d9a441; stroke-width: 2; stroke-dasharray: 5 4; }}
  .phair {{ stroke: #3a424e; stroke-width: 1; stroke-dasharray: 2 3; }}
  .pdot-o {{ fill: #34d399; stroke: #171a21; stroke-width: 2; }}
  .pdot-c {{ fill: #d9a441; stroke: #171a21; stroke-width: 2; }}
  @media (max-width: 760px) {{ .proj-grid {{ grid-template-columns: 1fr; }} }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .dot.ok {{ background: #34d399; }}
  .dot.idle {{ background: #6b7280; }}
  .dot.warn {{ background: #fbbf24; }}
  .dot.down {{ background: #f87171; }}
  .state {{ font-size: 12px; font-weight: 600; }}
  .state.ok {{ color: #34d399; }}
  .state.idle {{ color: #9ca3af; }}
  .state.warn {{ color: #fbbf24; }}
  .state.down {{ color: #f87171; }}
  .muted {{ color: #8b91a0; text-align: center; padding: 18px; }}
  footer {{ color: #6b7280; font-size: 12px; text-align: center; margin-top: 8px; }}
  #sndbtn {{ position: fixed; top: 12px; right: 12px; z-index: 50;
             background: #171a21; color: #e6e8eb; border: 1px solid #262b36;
             border-radius: 8px; padding: 7px 12px; font-size: 13px;
             cursor: pointer; }}
  #sndbtn:hover {{ border-color: #3b4252; }}
  @media (max-width: 900px) {{
    .toprow {{ grid-template-columns: 1fr; }}
    .maingrid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<button id="sndbtn">🔇 Ton aus</button>
<div class="wrap" id="wrap">
  <header>
    <h1>📊 Hurz Trading Dashboard</h1>
    <span class="updated"><b>Stand: {generated}</b> · Zeitraum: {period} · Auto-Update 30s</span>
  </header>
  <div class="toprow">
    <div class="panel status">
      <h3>Bot-Status</h3>
      {status}
    </div>
    <div class="cards">{cards}</div>
  </div>
  <span id="last-trade" data-id="{latest_id}" data-win="{latest_win}" hidden></span>
  <div class="maingrid">
    <div class="col">
      <div class="panel">
        <h3>Kumulierter realisierter PnL ({period})</h3>
        {chart}
      </div>
      {strategy_perf}
    </div>
    <div class="col">
      <div class="panel">
        <h3>Offene Positionen</h3>
        <table>
          <thead><tr><th>Platform</th><th>Pair</th><th>Strategie</th>
            <th>Richtung</th><th>Alter</th><th>Rest</th></tr></thead>
          <tbody>{open_rows}</tbody>
        </table>
      </div>
      <div class="panel">
        <h3>Letzte abgeschlossene Trades</h3>
        <table>
          <thead><tr><th>Abschluss</th><th>Platform</th><th>Pair</th><th>Strategie</th>
            <th>Ergebnis</th><th>PnL</th></tr></thead>
          <tbody>{recent_rows}</tbody>
        </table>
      </div>
    </div>
  </div>
  {projection}
  <footer>hurz · statisch generiert · Auto-Update alle 30s · Zeiten in Berliner Zeit ·
    Stats zeigen nur aktive Strategien (Mean-Reversion 2026-07-10 stillgelegt, läuft aus)</footer>
</div>
<script>
  // Sound on trade close: SUCCESS (win) = ascending chime, FAIL (loss) =
  // low descending tone. Browsers block audio until a user gesture, so the
  // 🔊 button primes it. Refresh is done via fetch+swap (not full reload)
  // so the audio context survives — falls back to reload if fetch is
  // blocked (e.g. file:// URLs).
  let actx = null;
  let soundOn = (localStorage.getItem('hurz_sound') === '1');
  function ensureCtx() {{
    if (!actx) {{ try {{ actx = new (window.AudioContext || window.webkitAudioContext)(); }} catch (e) {{}} }}
    if (actx && actx.state === 'suspended') actx.resume();
  }}
  function beep(win) {{
    if (!soundOn) return; ensureCtx(); if (!actx) return;
    const t0 = actx.currentTime;
    const seq = win ? [[660, 0.0], [880, 0.14]] : [[300, 0.0], [200, 0.16]];
    for (const [f, dt] of seq) {{
      const o = actx.createOscillator(), g = actx.createGain();
      o.type = 'sine'; o.frequency.value = f;
      o.connect(g); g.connect(actx.destination);
      g.gain.setValueAtTime(0.0001, t0 + dt);
      g.gain.exponentialRampToValueAtTime(0.25, t0 + dt + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.18);
      o.start(t0 + dt); o.stop(t0 + dt + 0.2);
    }}
  }}
  const btn = document.getElementById('sndbtn');
  function updBtn() {{ btn.textContent = soundOn ? '🔊 Ton an' : '🔇 Ton aus'; }}
  btn.addEventListener('click', () => {{
    soundOn = !soundOn; localStorage.setItem('hurz_sound', soundOn ? '1' : '0'); updBtn();
    if (soundOn) {{ ensureCtx(); beep(true); }}  // gesture unlocks audio + test chime
  }});
  updBtn();
  // Detect a newly-closed trade across updates via its stable id.
  function checkTrade() {{
    const el = document.getElementById('last-trade'); if (!el) return;
    const id = el.dataset.id; if (!id) return;
    const win = el.dataset.win === '1';
    const seen = localStorage.getItem('hurz_last_trade');
    if (seen !== null && seen !== id) beep(win);   // new close since last view
    localStorage.setItem('hurz_last_trade', id);
  }}
  checkTrade();
  // Refresh content every 30s without a full reload (keeps audio alive).
  async function refresh() {{
    try {{
      const r = await fetch(window.location.pathname + '?_=' + Date.now(), {{ cache: 'no-store' }});
      if (!r.ok) throw 0;
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      const nw = doc.getElementById('wrap');
      if (!nw) throw 0;
      document.getElementById('wrap').innerHTML = nw.innerHTML;
      // Keep styles current too, so CSS/layout changes apply without a full reload.
      const ns = doc.querySelector('style'), os = document.querySelector('style');
      if (ns && os && ns.textContent !== os.textContent) os.textContent = ns.textContent;
      checkTrade();
    }} catch (e) {{
      location.reload();  // fetch blocked (file://) → plain reload
    }}
  }}
  setInterval(refresh, 30000);
</script>
</body>
</html>"""


def main() -> int:
    days = _DEFAULT_DAYS
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        if arg in ("all", "max", "0"):
            days = None
        else:
            try:
                n = int(arg)
                days = n if n > 0 else None
            except ValueError:
                pass
    data = _fetch(days)
    html = _render_html(data, days)
    # Atomic write: the 5-min loop and the bot's hourly heartbeat hook
    # can both regenerate concurrently — write to a temp file and rename
    # so a reader never sees a half-written page.
    tmp_path = f"{_OUT_PATH}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp_path, _OUT_PATH)
    window = "all-time" if days is None else f"{days}d window"
    print(f"✓ dashboard written to {_OUT_PATH} "
          f"({len(data['closed'])} closed trades, {window})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
