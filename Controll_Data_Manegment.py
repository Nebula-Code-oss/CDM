
"""
net_monitor.py — Friendly live network in/out dashboard.

Shows a clean, auto-refreshing table (no scrolling spam) with:
  - Per-interface upload/download speed and totals, with simple bar graphs
  - Optional: top processes by active connection count (needs sudo on Linux)

Requirements:
    pip install psutil rich --break-system-packages   # Debian/Ubuntu
    (or inside a venv: pip install psutil rich)

Run:
    python3 net_monitor.py
    python3 net_monitor.py --processes           # add process view (may need sudo)
    python3 net_monitor.py --interval 1          # recalc rates every 1s

Search / filter:
    python3 net_monitor.py --name eth0                     # only interfaces/processes matching "eth0"
    python3 net_monitor.py --processes --name chrome       # only processes named like "chrome"
    python3 net_monitor.py --min-rate 50                   # only interfaces sending/receiving >= 50 KB/s
    python3 net_monitor.py --name wlan0 --min-rate 10       # combine filters

Press Ctrl+C to quit.
"""

import argparse
import time
import psutil
from datetime import datetime

from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.console import Group
from rich.text import Text


def human_bytes(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} PB"


def speed_bar(rate: float, max_rate: float, width: int = 20) -> str:
    """Return a simple text bar scaled to the fastest current rate."""
    if max_rate <= 0:
        filled = 0
    else:
        filled = int(width * min(rate / max_rate, 1.0))
    return "█" * filled + "░" * (width - filled)


def build_interface_table(prev, current, interval, name_filter=None, min_rate_kb=0.0):
    rates = {}
    for name, stats in current.items():
        if name in prev:
            rates[name] = {
                "sent": (stats.bytes_sent - prev[name].bytes_sent) / interval,
                "recv": (stats.bytes_recv - prev[name].bytes_recv) / interval,
                "total_sent": stats.bytes_sent,
                "total_recv": stats.bytes_recv,
            }

    # Skip interfaces with no traffic at all, unless it's the only one
    active = {k: v for k, v in rates.items() if v["sent"] or v["recv"] or v["total_sent"] or v["total_recv"]}
    display = active or rates

    # Apply search filters
    if name_filter:
        display = {k: v for k, v in display.items() if name_filter.lower() in k.lower()}
    if min_rate_kb > 0:
        min_rate_bytes = min_rate_kb * 1024
        display = {k: v for k, v in display.items() if max(v["sent"], v["recv"]) >= min_rate_bytes}

    max_rate = max((max(v["sent"], v["recv"]) for v in display.values()), default=0)

    title = "Network Interfaces"
    if name_filter or min_rate_kb > 0:
        bits = []
        if name_filter:
            bits.append(f"name~'{name_filter}'")
        if min_rate_kb > 0:
            bits.append(f"rate>={min_rate_kb}KB/s")
        title += f"  (filtered: {', '.join(bits)})"

    table = Table(title=title, expand=True, show_lines=False)
    table.add_column("Interface", style="bold cyan")
    table.add_column("Download", justify="right")
    table.add_column("", width=22)
    table.add_column("Upload", justify="right")
    table.add_column("", width=22)
    table.add_column("Total Down", justify="right", style="dim")
    table.add_column("Total Up", justify="right", style="dim")

    if not display:
        table.add_row("(no match)", "-", "", "-", "", "-", "-")
        return table

    for name, v in sorted(display.items(), key=lambda kv: -max(kv[1]["sent"], kv[1]["recv"])):
        table.add_row(
            name,
            f"{human_bytes(v['recv'])}/s",
            Text(speed_bar(v["recv"], max_rate), style="green"),
            f"{human_bytes(v['sent'])}/s",
            Text(speed_bar(v["sent"], max_rate), style="magenta"),
            human_bytes(v["total_recv"]),
            human_bytes(v["total_sent"]),
        )

    return table


def build_process_table(name_filter=None):
    title = "Top Active Connections by Process"
    if name_filter:
        title += f"  (filtered: name~'{name_filter}')"
    table = Table(title=title, expand=True)
    table.add_column("Process", style="bold yellow")
    table.add_column("PID", justify="right")
    table.add_column("Connections", justify="right")
    table.add_column("Example remote address", style="dim")

    try:
        conns = [c for c in psutil.net_connections(kind="inet") if c.status == psutil.CONN_ESTABLISHED and c.pid]
    except (psutil.AccessDenied, PermissionError):
        table.add_row("(permission denied — try sudo)", "-", "-", "-")
        return table

    by_pid = {}
    for c in conns:
        by_pid.setdefault(c.pid, []).append(c)

    if not by_pid:
        table.add_row("(no established connections)", "-", "-", "-")
        return table

    rows = []
    for pid, clist in by_pid.items():
        try:
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = "?"
        if name_filter and name_filter.lower() not in name.lower():
            continue
        example = clist[0].raddr
        example_str = f"{example.ip}:{example.port}" if example else "-"
        rows.append((name, pid, len(clist), example_str))

    if not rows:
        table.add_row("(no match)", "-", "-", "-")
        return table

    rows.sort(key=lambda r: -r[2])
    for name, pid, count, example_str in rows[:10]:
        table.add_row(name, str(pid), str(count), example_str)

    return table


def build_view(prev, current, interval, show_processes, name_filter=None, min_rate_kb=0.0):
    header = Text(
        f"Live Network Monitor   |   updated {datetime.now().strftime('%H:%M:%S')}   |   Ctrl+C to quit",
        style="bold white on blue",
        justify="center",
    )
    parts = [header, build_interface_table(prev, current, interval, name_filter, min_rate_kb)]
    if show_processes:
        parts.append(build_process_table(name_filter))
    return Panel(Group(*parts), border_style="blue")


def main():
    parser = argparse.ArgumentParser(description="Friendly live network monitor")
    parser.add_argument("--interval", type=float, default=1.0,
                         help="How often to recalculate bandwidth rates, in seconds (default: 1)")
    parser.add_argument("--processes", action="store_true", help="Also show top processes by connection count")
    parser.add_argument("--name", type=str, default=None,
                         help="Search/filter by interface or process name (case-insensitive, partial match). "
                              "Example: --name eth0   or   --name chrome")
    parser.add_argument("--min-rate", type=float, default=0.0,
                         help="Only show interfaces sending or receiving at least this many KB/s. "
                              "Example: --min-rate 50")
    args = parser.parse_args()

    prev = psutil.net_io_counters(pernic=True)
    prev_time = time.time()

    # Display refresh is decoupled from data sampling so the screen feels
    # continuously alive even though bandwidth is recalculated every --interval seconds.
    with Live(refresh_per_second=10, screen=True) as live:
        try:
            while True:
                time.sleep(0.1)
                now = time.time()
                elapsed = now - prev_time
                if elapsed >= args.interval:
                    current = psutil.net_io_counters(pernic=True)
                    live.update(build_view(prev, current, elapsed, args.processes, args.name, args.min_rate))
                    prev, prev_time = current, now
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()




