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
from datetime import datetime, timezone

import mysql.connector
from dotenv import load_dotenv

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
              {win}
            GROUP BY platform
        """, win_params)
        alltime = _rows(cur, """
            SELECT platform, ROUND(SUM(realized_pnl), 2) AS pnl
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
            GROUP BY platform
        """)
        open_pos = _rows(cur, """
            SELECT platform, pair, strategy, direction, entry_price, created_at
            FROM spot_trades
            WHERE accepted=1 AND exit_time IS NULL AND deal_id IS NOT NULL
            ORDER BY created_at ASC
        """)
        recent = _rows(cur, """
            SELECT platform, pair, strategy, direction,
                   entry_price, exit_price, outcome, realized_pnl, exit_time
            FROM spot_trades
            WHERE accepted=1 AND realized_pnl IS NOT NULL
            ORDER BY exit_time DESC
            LIMIT 15
        """)
        return {
            "closed": closed, "summary": summary, "alltime": alltime,
            "open": open_pos, "recent": recent,
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
    first = closed[0]["exit_time"]
    last = closed[-1]["exit_time"]
    span = (last - first).days
    return f"seit {first:%Y-%m-%d} ({span} Tage)"


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
        label = datetime.fromtimestamp(xv, tz=timezone.utc).strftime("%m-%d")
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
    for plat in ("capital_com", "kraken_futures"):
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


def _render_open(open_pos: list) -> str:
    if not open_pos:
        return '<tr><td colspan="5" class="muted">Keine offenen Positionen.</td></tr>'
    now = datetime.now(timezone.utc)
    out = []
    for p in open_pos:
        ct = p["created_at"]
        ct = ct.replace(tzinfo=timezone.utc) if ct.tzinfo is None else ct
        age_h = (now - ct).total_seconds() / 3600
        d = "Long" if int(p["direction"]) == 1 else "Short"
        out.append(f"""<tr>
          <td>{_LABELS.get(p['platform'], p['platform'])}</td>
          <td>{p['pair']}</td>
          <td>{p['strategy']}</td>
          <td class="{'pos' if d=='Long' else 'neg'}">{d}</td>
          <td>{age_h:.1f}h</td></tr>""")
    return "".join(out)


def _render_recent(recent: list) -> str:
    if not recent:
        return '<tr><td colspan="6" class="muted">Noch keine Trades.</td></tr>'
    out = []
    for r in recent:
        d = "Long" if int(r["direction"]) == 1 else "Short"
        when = r["exit_time"].strftime("%m-%d %H:%M") if r["exit_time"] else "—"
        out.append(f"""<tr>
          <td>{when}</td>
          <td>{_LABELS.get(r['platform'], r['platform'])}</td>
          <td>{r['pair']}</td>
          <td>{r['strategy']}</td>
          <td>{r['outcome']}</td>
          <td class="{_money_class(r['realized_pnl'])}">{_fmt_money(r['realized_pnl'])}</td>
        </tr>""")
    return "".join(out)


# (label, pid_file, log_file) for each bot session.
_BOTS = [
    ("Capital.com", "tmp/paper_session.pid", "tmp/paper_session.log"),
    ("Kraken Futures", "tmp/kraken_futures_session.pid",
     "tmp/kraken_futures_session.log"),
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
    # Only flag holiday when a marker for TODAY is present (the bot logs
    # "BANK HOLIDAY <date>"); past holidays must not stick.
    holiday = f"BANK HOLIDAY {today}" in "\n".join(lines)
    hb_age = ((now - last_hb_dt).total_seconds() / 60
              if last_hb_dt is not None else None)
    act_age = ((now - last_any_dt).total_seconds() / 60
               if last_any_dt is not None else None)
    last_hb = (last_hb_dt.strftime("%Y-%m-%d %H:%M:%S")
               if last_hb_dt is not None else None)
    last_act = (last_any_dt.strftime("%Y-%m-%d %H:%M:%S")
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
        "age_min": hb_age,
    }


def _render_status() -> str:
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
          </div>
        </div>""")
    return "".join(rows)


def _render_html(data: dict, days) -> str:
    period = _period_label(days, data["closed"])
    series = _cumulative_series(data["closed"])
    chart = _render_chart(series)
    status = _render_status()
    cards = _render_cards(data["summary"], data["alltime"], data["open"], period)
    open_rows = _render_open(data["open"])
    recent_rows = _render_recent(data["recent"])
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hurz Trading Dashboard</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: #f4f5f7; color: #1a1d24; }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 24px 18px 48px; }}
  header {{ display: flex; align-items: baseline; justify-content: space-between;
            flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }}
  h1 {{ font-size: 20px; margin: 0; }}
  .updated {{ color: #6b7280; font-size: 13px; }}
  .cards {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
            margin-bottom: 20px; }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
           padding: 16px 18px; }}
  .card h2 {{ font-size: 14px; margin: 0 0 6px; color: #374151;
              font-weight: 600; }}
  .big {{ font-size: 28px; font-weight: 700; }}
  .sub {{ color: #9ca3af; font-size: 12px; margin-bottom: 10px; }}
  .row {{ display: flex; justify-content: space-between; font-size: 13px;
          padding: 3px 0; border-top: 1px solid #f3f4f6; }}
  .panel {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
            padding: 16px 18px; margin-bottom: 20px; }}
  .panel h3 {{ font-size: 14px; margin: 0 0 12px; color: #374151; }}
  .chart {{ width: 100%; height: auto; }}
  .grid {{ stroke: #eef0f2; stroke-width: 1; }}
  .zero {{ stroke: #9ca3af; stroke-width: 1; stroke-dasharray: 3 3; }}
  .ylab, .xlab {{ fill: #9ca3af; font-size: 11px; }}
  .legend {{ display: flex; gap: 16px; margin-top: 8px; padding-left: 56px; }}
  .lg {{ font-size: 12px; color: #4b5563; display: flex; align-items: center;
         gap: 5px; }}
  .lg i {{ width: 11px; height: 11px; border-radius: 2px; display: inline-block; }}
  .nodata {{ color: #9ca3af; padding: 40px; text-align: center; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid #f3f4f6; }}
  th {{ color: #6b7280; font-weight: 600; font-size: 12px; }}
  .pos {{ color: #16a34a; }}
  .neg {{ color: #dc2626; }}
  .status .statusrow {{ padding: 8px 0; border-top: 1px solid #f3f4f6; }}
  .status .statusrow:first-of-type {{ border-top: none; }}
  .stitle {{ display: flex; align-items: center; gap: 8px; font-size: 14px; }}
  .smeta {{ display: flex; flex-wrap: wrap; gap: 14px; margin-top: 4px;
            padding-left: 18px; color: #6b7280; font-size: 12px; }}
  .dim {{ color: #9ca3af; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .dot.ok {{ background: #16a34a; }}
  .dot.idle {{ background: #6b7280; }}
  .dot.warn {{ background: #f59e0b; }}
  .dot.down {{ background: #dc2626; }}
  .state {{ font-size: 12px; font-weight: 600; }}
  .state.ok {{ color: #16a34a; }}
  .state.idle {{ color: #6b7280; }}
  .state.warn {{ color: #b45309; }}
  .state.down {{ color: #dc2626; }}
  .muted {{ color: #9ca3af; text-align: center; padding: 18px; }}
  footer {{ color: #9ca3af; font-size: 12px; text-align: center; margin-top: 8px; }}
  @media (max-width: 640px) {{ .cards {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📊 Hurz Trading Dashboard</h1>
    <span class="updated">Aktualisiert: {generated} · Zeitraum: {period}</span>
  </header>
  <div class="panel status">
    <h3>Bot-Status</h3>
    {status}
  </div>
  <div class="cards">{cards}</div>
  <div class="panel">
    <h3>Kumulierter realisierter PnL ({period})</h3>
    {chart}
  </div>
  <div class="panel">
    <h3>Offene Positionen</h3>
    <table>
      <thead><tr><th>Platform</th><th>Pair</th><th>Strategie</th>
        <th>Richtung</th><th>Alter</th></tr></thead>
      <tbody>{open_rows}</tbody>
    </table>
  </div>
  <div class="panel">
    <h3>Letzte abgeschlossene Trades</h3>
    <table>
      <thead><tr><th>Zeit</th><th>Platform</th><th>Pair</th><th>Strategie</th>
        <th>Ergebnis</th><th>PnL</th></tr></thead>
      <tbody>{recent_rows}</tbody>
    </table>
  </div>
  <footer>hurz · statisch generiert · stündliche Aktualisierung</footer>
</div>
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
