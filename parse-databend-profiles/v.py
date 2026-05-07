#!/usr/bin/env python3

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


DEFAULT_PROFILE_PATH = ".databend/logs_1/profiles"
NODE_WIDTH = 250
NODE_HEIGHT = 118
X_GAP = 70
Y_GAP = 82
MARGIN = 36


def iter_profile_files(profile_path):
    path = Path(profile_path)
    if path.is_file():
        yield path
        return

    if not path.exists():
        raise FileNotFoundError(f"profile path does not exist: {profile_path}")

    for child in sorted(path.iterdir()):
        if child.is_file():
            yield child


def iter_records(profile_path):
    for filename in iter_profile_files(profile_path):
        with filename.open("r", encoding="utf-8") as file:
            for line_no, line in enumerate(file, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as err:
                    print(
                        f"skip invalid json: {filename}:{line_no}: {err}",
                        file=sys.stderr,
                    )
                    continue
                if isinstance(record, dict) and "query_id" in record:
                    yield filename, line_no, record


def load_query_details(profile_path):
    details_dir = Path(profile_path).parent / "query-details"
    mapping = {}
    if not details_dir.is_dir():
        return mapping
    for child in sorted(details_dir.iterdir()):
        if not child.is_file():
            continue
        with child.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                qid = record.get("query_id")
                if qid:
                    mapping[qid] = {
                        "query_text": record.get("query_text", ""),
                        "query_start_time": record.get("query_start_time", ""),
                    }
    return mapping


def list_query_ids(profile_path):
    details = load_query_details(profile_path)
    rows = []
    for filename, line_no, record in iter_records(profile_path):
        qid = str(record.get("query_id", ""))
        detail = details.get(qid, {})
        rows.append(
            (
                qid,
                len(record.get("profiles") or []),
                detail.get("query_text", ""),
                detail.get("query_start_time", ""),
            )
        )
    rows.sort(key=lambda r: r[3])
    return rows


def find_query_profile(profile_path, query_id):
    matches = []
    for filename, line_no, record in iter_records(profile_path):
        if record.get("query_id") == query_id:
            matches.append((filename, line_no, record))
    if not matches:
        raise ValueError(f"query id not found: {query_id}")
    if len(matches) > 1:
        print(
            f"found {len(matches)} records for query id {query_id}; using the last one",
            file=sys.stderr,
        )
    return matches[-1]


def desc_by_index(statistics_desc):
    result = {}
    for name, desc in (statistics_desc or {}).items():
        index = desc.get("index")
        if isinstance(index, int):
            result[index] = {
                "name": name,
                "display_name": desc.get("display_name") or name,
                "unit": desc.get("unit") or "",
                "plain": bool(desc.get("plain_statistics")),
            }
    return result


def human_bytes(value):
    value = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PiB"


def human_nanos(value):
    value = float(value)
    if value < 1_000:
        return f"{int(value)} ns"
    if value < 1_000_000:
        return f"{value / 1_000:.2f} us"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.2f} ms"
    return f"{value / 1_000_000_000:.2f} s"


def format_metric(value, unit):
    if unit == "Bytes":
        return human_bytes(value)
    if unit == "NanoSeconds":
        return human_nanos(value)
    if unit in {"Rows", "Count"}:
        return f"{int(value):,}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def profile_statistics(profile, index_desc):
    stats = []
    for index, value in enumerate(profile.get("statistics") or []):
        if not value:
            continue
        desc = index_desc.get(index, {})
        unit = desc.get("unit", "")
        stats.append(
            {
                "key": desc.get("name") or str(index),
                "name": desc.get("display_name") or desc.get("name") or str(index),
                "raw": value,
                "unit": unit,
                "value": format_metric(value, unit),
            }
        )
    return stats


def metric_value(profile, index_desc, metric_name):
    for index, value in enumerate(profile.get("statistics") or []):
        desc = index_desc.get(index, {})
        if desc.get("name") == metric_name:
            return value or 0
    return 0


def preferred_stats(stats):
    priority = [
        "CpuTime",
        "WaitTime",
        "OutputRows",
        "OutputBytes",
        "ScanBytes",
        "ExchangeRows",
        "ExchangeBytes",
        "MemoryUsage",
        "LocalSpillWriteBytes",
        "RemoteSpillWriteBytes",
    ]
    rank = {name: index for index, name in enumerate(priority)}
    return sorted(stats, key=lambda item: (rank.get(item["key"], 999), item["key"]))[:6]


def normalize_profiles(record):
    index_desc = desc_by_index(record.get("statistics_desc"))
    nodes = {}
    for profile in record.get("profiles") or []:
        node_id = profile.get("id")
        if node_id is None:
            continue
        stats = profile_statistics(profile, index_desc)
        nodes[node_id] = {
            "id": node_id,
            "parent_id": profile.get("parent_id"),
            "name": profile.get("name") or "",
            "title": profile.get("title") or "",
            "labels": profile.get("labels") or [],
            "errors": profile.get("errors") or [],
            "statistics": stats,
            "top_statistics": preferred_stats(stats),
            "cpu_time": metric_value(profile, index_desc, "CpuTime"),
            "output_rows": metric_value(profile, index_desc, "OutputRows"),
            "output_bytes": metric_value(profile, index_desc, "OutputBytes"),
        }
    return nodes


def build_tree(nodes):
    children = defaultdict(list)
    roots = []
    for node_id, node in nodes.items():
        parent_id = node["parent_id"]
        if parent_id is None or parent_id not in nodes:
            roots.append(node_id)
        else:
            children[parent_id].append(node_id)

    roots.sort()
    for child_ids in children.values():
        child_ids.sort()

    positions = {}
    next_x = 0

    def place(node_id, depth):
        nonlocal next_x
        child_ids = children.get(node_id, [])
        if not child_ids:
            x = next_x
            next_x += NODE_WIDTH + X_GAP
        else:
            child_xs = [place(child_id, depth + 1) for child_id in child_ids]
            x = (child_xs[0] + child_xs[-1]) / 2
        positions[node_id] = {
            "x": x + MARGIN,
            "y": depth * (NODE_HEIGHT + Y_GAP) + MARGIN,
            "depth": depth,
        }
        return x

    for root in roots:
        place(root, 0)
        next_x += X_GAP

    max_x = max((pos["x"] for pos in positions.values()), default=0)
    max_y = max((pos["y"] for pos in positions.values()), default=0)
    width = int(max_x + NODE_WIDTH + MARGIN)
    height = int(max_y + NODE_HEIGHT + MARGIN)
    return roots, children, positions, width, height


def esc(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def text_preview(value, size):
    value = " ".join(str(value).split())
    if len(value) <= size:
        return value
    return value[: size - 1] + "..."


def cpu_fill(cpu_time, max_cpu):
    if max_cpu <= 0:
        return "#f8fafc"
    ratio = math.sqrt(cpu_time / max_cpu)
    lightness = 98 - ratio * 22
    return f"hsl(204, 80%, {lightness:.1f}%)"


def render_svg(nodes, children, positions, width, height):
    max_cpu = max((node["cpu_time"] for node in nodes.values()), default=0)
    edge_parts = [
        """
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"></path>
          </marker>
        </defs>
        """
    ]

    for parent_id, child_ids in children.items():
        parent = positions[parent_id]
        px = parent["x"] + NODE_WIDTH / 2
        py = parent["y"] + NODE_HEIGHT
        for child_id in child_ids:
            child = positions[child_id]
            cx = child["x"] + NODE_WIDTH / 2
            cy = child["y"]
            middle_y = (py + cy) / 2
            path = f"M{cx:.1f},{cy:.1f} C{cx:.1f},{middle_y:.1f} {px:.1f},{middle_y:.1f} {px:.1f},{py:.1f}"
            edge_parts.append(
                f'<path class="edge" d="{path}" marker-end="url(#arrow)"></path>'
            )

    node_parts = []
    for node_id in sorted(nodes, key=lambda item: (positions[item]["y"], positions[item]["x"])):
        node = nodes[node_id]
        pos = positions[node_id]
        stats = node["top_statistics"]
        stat_text = " | ".join(f'{item["name"]}: {item["value"]}' for item in stats[:3])
        fill = cpu_fill(node["cpu_time"], max_cpu)
        node_parts.append(
            f"""
            <g class="profile-node" data-node-id="{esc(node_id)}" tabindex="0">
              <rect x="{pos['x']:.1f}" y="{pos['y']:.1f}" width="{NODE_WIDTH}"
                    height="{NODE_HEIGHT}" rx="8" fill="{fill}"></rect>
              <foreignObject x="{pos['x'] + 12:.1f}" y="{pos['y'] + 10:.1f}"
                             width="{NODE_WIDTH - 24}" height="{NODE_HEIGHT - 20}">
                <div xmlns="http://www.w3.org/1999/xhtml" class="node-body">
                  <div class="node-title">#{esc(node_id)} {esc(node['name'])}</div>
                  <div class="node-subtitle">{esc(text_preview(node['title'], 76))}</div>
                  <div class="node-stats">{esc(stat_text)}</div>
                </div>
              </foreignObject>
            </g>
            """
        )

    return f"""
    <svg id="profile-graph" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
      {''.join(edge_parts)}
      {''.join(node_parts)}
    </svg>
    """


def html_template():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Databend Query Profile __QUERY_ID__</title>
  <style>
    :root {
      --border: #d7dee8;
      --ink: #172033;
      --muted: #5b677a;
      --panel: #ffffff;
      --bg: #f5f7fb;
      --accent: #0f6fbf;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      padding: 18px 22px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 20px;
      font-weight: 680;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      color: var(--muted);
      font-size: 13px;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 390px;
      min-height: calc(100vh - 80px);
    }
    .graph-wrap {
      overflow: auto;
      padding: 22px;
    }
    .detail {
      border-left: 1px solid var(--border);
      background: var(--panel);
      padding: 18px;
      overflow: auto;
      max-height: calc(100vh - 80px);
      position: sticky;
      top: 0;
    }
    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      margin-bottom: 14px;
    }
    input {
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font: inherit;
    }
    svg {
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 8px;
      min-width: 100%;
    }
    .edge {
      fill: none;
      stroke: #64748b;
      stroke-width: 1.4;
    }
    .profile-node { cursor: pointer; outline: none; }
    .profile-node rect {
      stroke: #8da2b8;
      stroke-width: 1.2;
      filter: drop-shadow(0 1px 2px rgb(15 23 42 / 0.10));
    }
    .profile-node:hover rect,
    .profile-node.selected rect {
      stroke: var(--accent);
      stroke-width: 2.5;
    }
    .profile-node.dimmed { opacity: 0.22; }
    .node-body {
      width: 100%;
      height: 100%;
      overflow: hidden;
    }
    .node-title {
      font-weight: 700;
      font-size: 14px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .node-subtitle {
      margin-top: 5px;
      color: #334155;
      font-size: 12px;
      max-height: 34px;
      overflow: hidden;
    }
    .node-stats {
      margin-top: 8px;
      color: var(--muted);
      font-size: 11px;
      max-height: 32px;
      overflow: hidden;
    }
    .section { margin-top: 18px; }
    .section h2 {
      font-size: 13px;
      margin: 0 0 8px;
      color: #334155;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    td {
      padding: 6px 0;
      border-bottom: 1px solid #edf1f6;
      vertical-align: top;
    }
    td:first-child {
      color: var(--muted);
      padding-right: 14px;
      width: 42%;
    }
    .empty {
      color: var(--muted);
      font-style: italic;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8fafc;
      border: 1px solid #edf1f6;
      border-radius: 6px;
      padding: 10px;
      margin: 0;
      font-size: 12px;
    }
    @media (max-width: 960px) {
      main { grid-template-columns: 1fr; }
      .detail {
        border-left: 0;
        border-top: 1px solid var(--border);
        max-height: none;
        position: static;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>Databend Query Profile</h1>
    <div class="meta">
      <span><strong>query_id:</strong> __QUERY_ID__</span>
      <span><strong>nodes:</strong> __NODE_COUNT__</span>
      <span><strong>source:</strong> __SOURCE__</span>
    </div>
  </header>
  <main>
    <section class="graph-wrap">
      <div class="toolbar">
        <input id="filter" placeholder="Filter by id, name, title, label, or metric">
      </div>
      __SVG__
    </section>
    <aside class="detail">
      <div id="detail-panel"></div>
    </aside>
  </main>
  <script>
    const nodes = __NODES_JSON__;

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function row(left, right) {
      return `<tr><td>${escapeHtml(left)}</td><td>${right}</td></tr>`;
    }

    function renderTable(rows) {
      if (!rows.length) {
        return '<div class="empty">none</div>';
      }
      return `<table>${rows.join("")}</table>`;
    }

    function labelText(label) {
      const value = Array.isArray(label.value) ? label.value.join(", ") : label.value;
      return `${label.name}: ${value}`;
    }

    function renderNode(id) {
      const node = nodes[String(id)];
      if (!node) {
        return;
      }
      document.querySelectorAll(".profile-node").forEach((item) => {
        item.classList.toggle("selected", item.dataset.nodeId === String(id));
      });

      const basicRows = [
        row("id", escapeHtml(node.id)),
        row("parent_id", escapeHtml(node.parent_id ?? "null")),
        row("name", escapeHtml(node.name)),
        row("title", escapeHtml(node.title || "")),
      ];
      const statRows = node.statistics.map((item) => {
        return row(item.name, `${escapeHtml(item.value)} <span class="empty">raw=${escapeHtml(item.raw)}</span>`);
      });
      const labelRows = node.labels.map((item) => row(item.name, escapeHtml(
        Array.isArray(item.value) ? item.value.join(", ") : item.value
      )));
      const errors = node.errors.length
        ? `<pre>${escapeHtml(JSON.stringify(node.errors, null, 2))}</pre>`
        : '<div class="empty">none</div>';

      document.getElementById("detail-panel").innerHTML = `
        <div class="section"><h2>Node</h2>${renderTable(basicRows)}</div>
        <div class="section"><h2>Statistics</h2>${renderTable(statRows)}</div>
        <div class="section"><h2>Labels</h2>${renderTable(labelRows)}</div>
        <div class="section"><h2>Errors</h2>${errors}</div>
      `;
    }

    function nodeSearchText(node) {
      const labels = node.labels.map(labelText).join(" ");
      const stats = node.statistics.map((item) => `${item.key} ${item.name} ${item.value}`).join(" ");
      return `${node.id} ${node.parent_id ?? ""} ${node.name} ${node.title} ${labels} ${stats}`.toLowerCase();
    }

    document.querySelectorAll(".profile-node").forEach((item) => {
      item.addEventListener("click", () => renderNode(item.dataset.nodeId));
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          renderNode(item.dataset.nodeId);
        }
      });
    });

    document.getElementById("filter").addEventListener("input", (event) => {
      const needle = event.target.value.trim().toLowerCase();
      document.querySelectorAll(".profile-node").forEach((item) => {
        const node = nodes[item.dataset.nodeId];
        item.classList.toggle("dimmed", Boolean(needle) && !nodeSearchText(node).includes(needle));
      });
    });

    const firstId = Object.keys(nodes).sort((a, b) => Number(a) - Number(b))[0];
    if (firstId !== undefined) {
      renderNode(firstId);
    }
  </script>
</body>
</html>
"""


def render_html(record, source):
    nodes = normalize_profiles(record)
    if not nodes:
        raise ValueError("query profile record contains no profile nodes")
    _, children, positions, width, height = build_tree(nodes)
    svg = render_svg(nodes, children, positions, width, height)
    nodes_json = json.dumps({str(k): v for k, v in nodes.items()}, ensure_ascii=False)
    return (
        html_template()
        .replace("__QUERY_ID__", esc(record.get("query_id") or ""))
        .replace("__NODE_COUNT__", str(len(nodes)))
        .replace("__SOURCE__", esc(source))
        .replace("__SVG__", svg)
        .replace("__NODES_JSON__", nodes_json.replace("</", "<\\/"))
    )


def write_output(content, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return output


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Visualize Databend JSON query profile logs from "
            ".databend/logs_1/profiles as an HTML plan tree."
        )
    )
    parser.add_argument("query_id", nargs="?", help="query_id to visualize")
    parser.add_argument(
        "-p",
        "--profile-path",
        default=DEFAULT_PROFILE_PATH,
        help=(
            "optional override for profile log file or directory, "
            f"default: {DEFAULT_PROFILE_PATH}"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help="output html path, default: profile_<query_id>.html",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list query ids found in the profile path and exit",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.list:
        rows = list_query_ids(args.profile_path)
        if not rows:
            print("no query profile records found")
            return 1
        width = max(len(row[0]) for row in rows)
        for query_id, count, query_text, start_time in rows:
            sql_preview = " ".join(query_text.split())[:60]
            print(f"{query_id:<{width}}  profiles={count:<4}  {sql_preview}")
        return 0

    if not args.query_id:
        print("query_id is required unless --list is used", file=sys.stderr)
        return 2

    filename, line_no, record = find_query_profile(args.profile_path, args.query_id)
    source = f"{filename}:{line_no}"
    html = render_html(record, source)
    output = args.output or f"profile_{args.query_id}.html"
    write_output(html, output)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, ValueError) as err:
        print(err, file=sys.stderr)
        sys.exit(1)

