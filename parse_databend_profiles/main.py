#!/usr/bin/env python3
"""Parse and display Databend query profiles in a human-readable tree format."""

import json
import sys
from dataclasses import dataclass, field


def format_nanoseconds(ns: int) -> str:
    if ns == 0:
        return "0s"
    if ns < 1_000:
        return f"{ns}ns"
    if ns < 1_000_000:
        return f"{ns / 1_000:.2f}µs"
    if ns < 1_000_000_000:
        return f"{ns / 1_000_000:.2f}ms"
    return f"{ns / 1_000_000_000:.2f}s"


def format_milliseconds(ms: int) -> str:
    if ms == 0:
        return "0s"
    if ms < 1_000:
        return f"{ms}ms"
    return f"{ms / 1_000:.2f}s"


def format_bytes(b: int) -> str:
    if b == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.2f} {unit}" if b != int(b) else f"{int(b)} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def format_rows(n: int) -> str:
    if n == 0:
        return "0"
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.2f}K"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.2f}M"
    return f"{n / 1_000_000_000:.2f}B"


def format_count(n: int) -> str:
    return format_rows(n)


UNIT_FORMATTERS = {
    "NanoSeconds": format_nanoseconds,
    "MillisSeconds": format_milliseconds,
    "Bytes": format_bytes,
    "Rows": format_rows,
    "Count": format_count,
}


@dataclass
class StatDesc:
    name: str
    display_name: str
    index: int
    unit: str
    plain_statistics: bool


@dataclass
class ProfileNode:
    id: int
    name: str
    parent_id: int | None
    title: str
    labels: list[dict]
    statistics: list[int]
    errors: list[str]
    children: list["ProfileNode"] = field(default_factory=list)


def parse_statistics_desc(raw: dict) -> dict[int, StatDesc]:
    result = {}
    for key, val in raw.items():
        desc = StatDesc(
            name=key,
            display_name=val["display_name"],
            index=val["index"],
            unit=val["unit"],
            plain_statistics=val["plain_statistics"],
        )
        result[desc.index] = desc
    return result


def build_tree(profiles: list[dict]) -> list[ProfileNode]:
    nodes = {}
    for p in profiles:
        node = ProfileNode(
            id=p["id"],
            name=p["name"],
            parent_id=p["parent_id"],
            title=p["title"],
            labels=p["labels"],
            statistics=p["statistics"],
            errors=p.get("errors", []),
        )
        nodes[node.id] = node

    roots = []
    for node in nodes.values():
        if node.parent_id is not None and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node)
        else:
            roots.append(node)

    return roots

def format_stat_value(value: int, desc: StatDesc) -> str:
    formatter = UNIT_FORMATTERS.get(desc.unit)
    if formatter:
        return formatter(value)
    return str(value)


def print_tree(
    node: ProfileNode,
    stat_descs: dict[int, StatDesc],
    prefix: str = "",
    is_last: bool = True,
):
    connector = "└── " if is_last else "├── "
    print(f"{prefix}{connector}[{node.name}] {node.title}")

    child_prefix = prefix + ("    " if is_last else "│   ")

    # collect non-zero statistics
    stats_parts = []
    for i, val in enumerate(node.statistics):
        if val == 0:
            continue
        desc = stat_descs.get(i)
        if desc:
            stats_parts.append(f"{desc.display_name}: {format_stat_value(val, desc)}")

    if stats_parts:
        print(f"{child_prefix}  {', '.join(stats_parts)}")

    if node.errors:
        print(f"{child_prefix}  ERRORS: {node.errors}")

    for i, child in enumerate(node.children):
        print_tree(child, stat_descs, child_prefix, i == len(node.children) - 1)


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    query_id = data["query_id"]
    stat_descs = parse_statistics_desc(data["statistics_desc"])
    roots = build_tree(data["profiles"])

    print(f"Query ID: {query_id}")
    print()
    for i, root in enumerate(roots):
        print_tree(root, stat_descs, "", i == len(roots) - 1)


if __name__ == "__main__":
    main()
