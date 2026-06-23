#!/usr/bin/env python3
"""Generate time-series graphs from MariaDB AI DBA daemon samples or snapshots.

Usage:
    python3 graph.py --samples-dir ./snapshots/samples --output-dir ./graphs
    python3 graph.py --snapshots-dir ./snapshots --output-dir ./graphs

Reads JSONL daemon samples (1-second resolution) or full snapshot files
(minutes/hours/days apart) and produces base64-encoded PNG graphs embedded
in a JSON manifest that the report generator can inline into HTML.
"""

import argparse
import base64
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


def safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_daemon_samples(samples_dir):
    """Load JSONL daemon sample files, return sorted list of dicts."""
    samples_dir = Path(samples_dir)
    if not samples_dir.exists():
        return []

    samples = []
    for f in sorted(samples_dir.glob("samples_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    sample = json.loads(line)
                    if "_error" not in sample and "ts" in sample:
                        samples.append(sample)
                except json.JSONDecodeError:
                    continue

    samples.sort(key=lambda s: s["ts"])
    return samples


def load_snapshot_series(snapshots_dir, hostname=None, port=None):
    """Load full snapshots as time-series points (lower resolution)."""
    snapshots_dir = Path(snapshots_dir)
    if not snapshots_dir.exists():
        return []

    points = []
    for f in sorted(snapshots_dir.glob("snapshot_*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            meta = data.get("meta", {})
            snap_host = meta.get("hostname", "")
            snap_port = data.get("server", {}).get("port", 3306)
            if hostname and snap_host != hostname:
                continue
            if port and snap_port != port:
                continue
            status = data.get("status_snapshot", {})
            if not status:
                continue
            status["ts"] = int(safe_float(
                datetime.fromisoformat(meta["timestamp"]).timestamp()
            )) if "timestamp" in meta else 0
            if status["ts"] == 0:
                continue
            points.append(status)
        except Exception:
            continue

    points.sort(key=lambda s: s["ts"])
    return points


# ---------------------------------------------------------------------------
# Rate computation (cumulative counters -> per-second rates)
# ---------------------------------------------------------------------------

def compute_rates(samples, var_names):
    """Convert cumulative counter samples into per-second rate series.

    Returns list of (timestamp, {var: rate_per_sec, ...}) tuples.
    """
    if len(samples) < 2:
        return []

    rates = []
    for i in range(1, len(samples)):
        prev = samples[i - 1]
        cur = samples[i]
        elapsed = safe_int(cur["ts"]) - safe_int(prev["ts"])
        if elapsed <= 0:
            continue

        point = {"ts": cur["ts"]}
        for var in var_names:
            prev_val = safe_int(prev.get(var, 0))
            cur_val = safe_int(cur.get(var, 0))
            delta = cur_val - prev_val
            if delta < 0:
                delta = 0
            point[var] = delta / elapsed
        rates.append(point)

    return rates


def extract_gauges(samples, var_names):
    """Extract point-in-time gauge values as time series."""
    series = []
    for s in samples:
        point = {"ts": s["ts"]}
        for var in var_names:
            point[var] = safe_int(s.get(var, 0))
        series.append(point)
    return series


# ---------------------------------------------------------------------------
# Graph definitions
# ---------------------------------------------------------------------------

GRAPH_DEFS = [
    {
        "id": "query_throughput",
        "title": "Query Throughput",
        "section": "performance",
        "type": "rate",
        "vars": ["QUESTIONS"],
        "labels": ["Queries/sec"],
        "ylabel": "queries/sec",
    },
    {
        "id": "statement_mix",
        "title": "Statement Mix",
        "section": "performance",
        "type": "rate",
        "vars": ["COM_SELECT", "COM_INSERT", "COM_UPDATE", "COM_DELETE"],
        "labels": ["SELECT", "INSERT", "UPDATE", "DELETE"],
        "ylabel": "statements/sec",
        "stacked": True,
    },
    {
        "id": "buffer_pool_pages",
        "title": "InnoDB Buffer Pool Pages",
        "section": "innodb",
        "type": "gauge",
        "vars": [
            "INNODB_BUFFER_POOL_PAGES_DATA",
            "INNODB_BUFFER_POOL_PAGES_DIRTY",
            "INNODB_BUFFER_POOL_PAGES_FREE",
        ],
        "labels": ["Data", "Dirty", "Free"],
        "ylabel": "pages (16 KB each)",
        "stacked": True,
    },
    {
        "id": "checkpoint_age",
        "title": "InnoDB Checkpoint Age (% of max)",
        "section": "innodb",
        "type": "computed",
        "compute": "checkpoint_pct",
        "ylabel": "% of max checkpoint age",
    },
    {
        "id": "threads",
        "title": "Threads Connected / Running",
        "section": "connections",
        "type": "gauge",
        "vars": ["THREADS_CONNECTED", "THREADS_RUNNING"],
        "labels": ["Connected", "Running"],
        "ylabel": "threads",
    },
    {
        "id": "tmp_disk_tables",
        "title": "Temporary Tables (Disk vs Total)",
        "section": "performance",
        "type": "rate",
        "vars": ["CREATED_TMP_DISK_TABLES", "CREATED_TMP_TABLES"],
        "labels": ["Disk temp tables/sec", "Total temp tables/sec"],
        "ylabel": "tables/sec",
    },
    {
        "id": "innodb_io",
        "title": "InnoDB I/O Operations",
        "section": "innodb",
        "type": "rate",
        "vars": ["INNODB_DATA_READS", "INNODB_DATA_WRITES", "INNODB_LOG_WRITES"],
        "labels": ["Data reads", "Data writes", "Log writes"],
        "ylabel": "operations/sec",
    },
    {
        "id": "innodb_row_ops",
        "title": "InnoDB Row Operations",
        "section": "innodb",
        "type": "rate",
        "vars": [
            "INNODB_ROWS_READ", "INNODB_ROWS_INSERTED",
            "INNODB_ROWS_UPDATED", "INNODB_ROWS_DELETED",
        ],
        "labels": ["Read", "Inserted", "Updated", "Deleted"],
        "ylabel": "rows/sec",
    },
    {
        "id": "history_list",
        "title": "InnoDB History List Length",
        "section": "innodb",
        "type": "gauge",
        "vars": ["INNODB_HISTORY_LIST_LENGTH"],
        "labels": ["History list length"],
        "ylabel": "transactions",
    },
    {
        "id": "table_locks",
        "title": "Table Locks",
        "section": "performance",
        "type": "rate",
        "vars": ["TABLE_LOCKS_IMMEDIATE", "TABLE_LOCKS_WAITED"],
        "labels": ["Immediate (granted)", "Waited"],
        "ylabel": "locks/sec",
    },
    # --- OS metrics ---
    {
        "id": "os_memory",
        "title": "Memory Usage",
        "section": "os",
        "type": "computed",
        "compute": "os_memory_pct",
        "ylabel": "% of total RAM",
    },
    {
        "id": "os_swap",
        "title": "Swap Usage",
        "section": "os",
        "type": "gauge",
        "vars": ["os_swap_used"],
        "labels": ["Swap used"],
        "ylabel": "MB",
        "scale": 1 / (1024 * 1024),
    },
    {
        "id": "os_load",
        "title": "System Load Average",
        "section": "os",
        "type": "gauge",
        "vars": ["os_load_1m", "os_load_5m"],
        "labels": ["1-min load", "5-min load"],
        "ylabel": "load",
    },
    {
        "id": "os_cpu",
        "title": "CPU Usage",
        "section": "os",
        "type": "computed",
        "compute": "os_cpu_pct",
        "ylabel": "% CPU",
    },
    {
        "id": "os_disk_latency",
        "title": "Disk I/O Latency",
        "section": "os",
        "type": "computed",
        "compute": "os_disk_latency",
        "ylabel": "ms per I/O",
    },
]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_graph(graph_def, samples, fig_width=10, fig_height=3.5):
    """Render a single graph and return base64-encoded PNG string."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import seaborn as sns
    except ImportError:
        return None

    sns.set_theme(style="whitegrid", palette="muted", font_scale=0.9)

    graph_type = graph_def["type"]

    if graph_type == "rate":
        series = compute_rates(samples, graph_def["vars"])
    elif graph_type == "gauge":
        series = extract_gauges(samples, graph_def["vars"])
    elif graph_type == "computed" and graph_def.get("compute") == "checkpoint_pct":
        series = []
        for s in samples:
            age = safe_int(s.get("INNODB_CHECKPOINT_AGE", 0))
            max_age = safe_int(s.get("INNODB_CHECKPOINT_MAX_AGE", 1))
            if max_age > 0:
                series.append({"ts": s["ts"], "pct": (age / max_age) * 100})
    elif graph_type == "computed" and graph_def.get("compute") == "os_memory_pct":
        series = []
        for s in samples:
            total = safe_int(s.get("os_mem_total", 0))
            avail = safe_int(s.get("os_mem_available", 0))
            if total > 0:
                used_pct = ((total - avail) / total) * 100
                series.append({"ts": s["ts"], "pct": used_pct})
    elif graph_type == "computed" and graph_def.get("compute") == "os_cpu_pct":
        series = []
        for i in range(1, len(samples)):
            prev, cur = samples[i-1], samples[i]
            dt = cur["ts"] - prev["ts"]
            if dt <= 0:
                continue
            p_total = sum(safe_int(prev.get(k, 0)) for k in
                          ("os_cpu_user", "os_cpu_nice", "os_cpu_system",
                           "os_cpu_idle", "os_cpu_iowait"))
            c_total = sum(safe_int(cur.get(k, 0)) for k in
                          ("os_cpu_user", "os_cpu_nice", "os_cpu_system",
                           "os_cpu_idle", "os_cpu_iowait"))
            d_total = c_total - p_total
            if d_total <= 0:
                continue
            d_idle = safe_int(cur.get("os_cpu_idle", 0)) - safe_int(prev.get("os_cpu_idle", 0))
            d_iowait = safe_int(cur.get("os_cpu_iowait", 0)) - safe_int(prev.get("os_cpu_iowait", 0))
            cpu_pct = ((d_total - d_idle) / d_total) * 100
            iowait_pct = (d_iowait / d_total) * 100
            series.append({"ts": cur["ts"], "cpu": cpu_pct, "iowait": iowait_pct})
    elif graph_type == "computed" and graph_def.get("compute") == "os_disk_latency":
        series = []
        for i in range(1, len(samples)):
            prev, cur = samples[i-1], samples[i]
            d_reads = safe_int(cur.get("os_disk_reads", 0)) - safe_int(prev.get("os_disk_reads", 0))
            d_writes = safe_int(cur.get("os_disk_writes", 0)) - safe_int(prev.get("os_disk_writes", 0))
            d_read_ms = safe_int(cur.get("os_disk_read_ms", 0)) - safe_int(prev.get("os_disk_read_ms", 0))
            d_write_ms = safe_int(cur.get("os_disk_write_ms", 0)) - safe_int(prev.get("os_disk_write_ms", 0))
            read_lat = (d_read_ms / d_reads) if d_reads > 0 else 0
            write_lat = (d_write_ms / d_writes) if d_writes > 0 else 0
            series.append({"ts": cur["ts"], "read_lat": read_lat, "write_lat": write_lat})
    else:
        return None

    if len(series) < 2:
        return None

    # Skip graphs where all data values are effectively zero
    compute = graph_def.get("compute", "")
    if graph_type in ("rate", "gauge"):
        all_max = 0
        for var in graph_def["vars"]:
            var_max = max(abs(s.get(var, 0)) for s in series)
            all_max = max(all_max, var_max)
        if all_max < 0.1:
            return None
    elif graph_type == "computed":
        if compute == "checkpoint_pct":
            if max(s.get("pct", 0) for s in series) < 0.01:
                return None
        elif compute == "os_memory_pct":
            if max(s.get("pct", 0) for s in series) < 0.01:
                return None
        elif compute == "os_cpu_pct":
            if max(s.get("cpu", 0) for s in series) < 0.1:
                return None
        elif compute == "os_disk_latency":
            if max(max(s.get("read_lat", 0), s.get("write_lat", 0)) for s in series) < 0.01:
                return None

    # Insert NaN at gaps (>3x median interval) to break lines
    import math
    intervals = [series[i]["ts"] - series[i-1]["ts"] for i in range(1, len(series))]
    if intervals:
        median_interval = sorted(intervals)[len(intervals) // 2]
        gap_threshold = max(median_interval * 3, 10)
        patched = [series[0]]
        for i in range(1, len(series)):
            if series[i]["ts"] - series[i-1]["ts"] > gap_threshold:
                gap_point = {"ts": series[i-1]["ts"] + 1}
                for key in series[i]:
                    if key != "ts":
                        gap_point[key] = float("nan")
                patched.append(gap_point)
            patched.append(series[i])
        series = patched

    timestamps = [datetime.fromtimestamp(s["ts"]) for s in series]

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    if graph_type == "computed" and compute == "checkpoint_pct":
        values = [s["pct"] for s in series]
        ax.fill_between(timestamps, values, alpha=0.3)
        ax.plot(timestamps, values, linewidth=1.5)
        ax.set_ylim(0, max(max(v for v in values if not math.isnan(v)) * 1.1, 10))
    elif graph_type == "computed" and compute == "os_memory_pct":
        values = [s["pct"] for s in series]
        ax.fill_between(timestamps, values, alpha=0.3, color="#d97a4a")
        ax.plot(timestamps, values, linewidth=1.5, color="#d97a4a")
        ax.set_ylim(0, 100)
    elif graph_type == "computed" and compute == "os_cpu_pct":
        cpu_vals = [s.get("cpu", 0) for s in series]
        iowait_vals = [s.get("iowait", 0) for s in series]
        ax.fill_between(timestamps, iowait_vals, alpha=0.4, label="I/O wait", color="#c05050")
        ax.fill_between(timestamps, cpu_vals, alpha=0.3, label="CPU", color="#4a90d9")
        ax.plot(timestamps, cpu_vals, linewidth=1.5, color="#4a90d9")
        ax.plot(timestamps, iowait_vals, linewidth=1, color="#c05050", linestyle="--")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
        ax.set_ylim(0, 100)
    elif graph_type == "computed" and compute == "os_disk_latency":
        read_vals = [s.get("read_lat", 0) for s in series]
        write_vals = [s.get("write_lat", 0) for s in series]
        ax.plot(timestamps, read_vals, label="Read latency", linewidth=1.5, color="#4a90d9")
        ax.plot(timestamps, write_vals, label="Write latency", linewidth=1.5, color="#d97a4a")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    elif graph_def.get("stacked"):
        scale = graph_def.get("scale", 1)
        all_series = {}
        for var, label in zip(graph_def["vars"], graph_def["labels"]):
            all_series[label] = [s.get(var, 0) * scale for s in series]
        ax.stackplot(timestamps, *all_series.values(),
                     labels=all_series.keys(), alpha=0.7)
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    else:
        scale = graph_def.get("scale", 1)
        for var, label in zip(graph_def["vars"], graph_def["labels"]):
            values = [s.get(var, 0) * scale for s in series]
            ax.plot(timestamps, values, label=label, linewidth=1.5)
        if len(graph_def["vars"]) > 1:
            ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

    ax.set_ylabel(graph_def.get("ylabel", ""), fontsize=9)
    ax.set_title(graph_def["title"], fontsize=11, fontweight="bold", pad=10)

    time_span = timestamps[-1] - timestamps[0]
    if time_span < timedelta(hours=2):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=max(1, int(time_span.seconds / 60 / 8))))
    elif time_span < timedelta(days=1):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, int(time_span.seconds / 3600 / 8))))
    elif time_span < timedelta(days=7):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        ax.xaxis.set_major_locator(mdates.DayLocator())
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

    fig.autofmt_xdate(rotation=30, ha="right")
    ax.tick_params(axis="both", labelsize=8)
    ax.margins(x=0.01)
    sns.despine(left=True, bottom=True)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)

    png_bytes = buf.getvalue()
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return b64


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_all_graphs(samples_dir=None, snapshots_dir=None,
                        hostname=None, port=None):
    """Generate all graphs, return dict of {graph_id: {title, section, png_base64}}."""
    samples = []
    source = "none"

    if samples_dir:
        samples = load_daemon_samples(samples_dir)
        if samples:
            source = "daemon"

    if not samples and snapshots_dir:
        samples = load_snapshot_series(snapshots_dir, hostname, port)
        if samples:
            source = "snapshots"

    if len(samples) < 2:
        return {"graphs": [], "source": "none", "sample_count": 0}

    time_span = samples[-1]["ts"] - samples[0]["ts"]

    results = []
    for gdef in GRAPH_DEFS:
        png_b64 = render_graph(gdef, samples)
        if png_b64:
            results.append({
                "id": gdef["id"],
                "title": gdef["title"],
                "section": gdef["section"],
                "png_base64": png_b64,
            })

    return {
        "graphs": results,
        "source": source,
        "sample_count": len(samples),
        "time_range": {
            "start": datetime.fromtimestamp(samples[0]["ts"]).isoformat(),
            "end": datetime.fromtimestamp(samples[-1]["ts"]).isoformat(),
            "seconds": time_span,
        },
    }


def inject_graphs_into_html(html_path, graphs):
    """Replace <!-- GRAPH:id --> markers in an HTML file with embedded PNG images."""
    with open(html_path) as f:
        html = f.read()

    injected = 0
    for g in graphs:
        marker = f"<!-- GRAPH:{g['id']} -->"
        if marker in html:
            img_tag = (
                f'<img src="data:image/png;base64,{g["png_base64"]}" '
                f'alt="{g["title"]}" '
                f'style="width:100%;max-width:750px;margin:12px 0;">'
            )
            html = html.replace(marker, img_tag)
            injected += 1

    # Also replace section-level markers that collect all graphs for a section
    sections = {}
    for g in graphs:
        sections.setdefault(g["section"], []).append(g)
    for section, section_graphs in sections.items():
        marker = f"<!-- GRAPHS:{section} -->"
        if marker in html:
            img_tags = "\n".join(
                f'<img src="data:image/png;base64,{g["png_base64"]}" '
                f'alt="{g["title"]}" '
                f'style="width:100%;max-width:750px;margin:12px 0;">'
                for g in section_graphs
            )
            html = html.replace(marker, img_tags)
            injected += len(section_graphs)

    with open(html_path, "w") as f:
        f.write(html)

    return injected


def main():
    parser = argparse.ArgumentParser(description="Generate MariaDB trending graphs")
    parser.add_argument("--samples-dir", help="Path to daemon JSONL samples directory")
    parser.add_argument("--snapshots-dir", help="Path to snapshot JSON directory")
    parser.add_argument("--hostname", help="Filter snapshots by hostname")
    parser.add_argument("--port", type=int, help="Filter snapshots by port")
    parser.add_argument("--output-dir", help="Write individual PNG files here (optional)")
    parser.add_argument("--inject", help="Inject graphs into this HTML file (replaces <!-- GRAPH:id --> markers)")
    args = parser.parse_args()

    if not args.samples_dir and not args.snapshots_dir:
        print("Error: provide --samples-dir and/or --snapshots-dir", file=sys.stderr)
        sys.exit(1)

    result = generate_all_graphs(
        samples_dir=args.samples_dir,
        snapshots_dir=args.snapshots_dir,
        hostname=args.hostname,
        port=args.port,
    )

    if args.inject and result["graphs"]:
        injected = inject_graphs_into_html(args.inject, result["graphs"])
        print(f"Injected {injected} graphs into {args.inject}", file=sys.stderr)

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for g in result["graphs"]:
            png_data = base64.b64decode(g["png_base64"])
            (out_dir / f"{g['id']}.png").write_bytes(png_data)

    # Summary to stdout (without base64 — keep it small for AI context)
    summary = {
        "source": result["source"],
        "sample_count": result["sample_count"],
        "time_range": result.get("time_range"),
        "graphs": [{"id": g["id"], "title": g["title"], "section": g["section"]}
                    for g in result["graphs"]],
    }
    if args.inject:
        summary["injected_into"] = args.inject
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
