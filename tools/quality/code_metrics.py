#!/usr/bin/env python3
"""
Compute code complexity metrics for the Python code in scripts, tools and tests.

Metrics, per file:
- Cyclomatic complexity of each function (lizard)
- Cognitive complexity of each function (cognitive_complexity)
- Maintainability Index (radon)

Usage:
    python tools/quality/code_metrics.py [--paths PATH ...] [--exclude-dir NAME ...]
        [--format text|json] [--top N] [--output FILE] [--fail-on-bad]
"""

from __future__ import annotations

import argparse
import ast
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Sequence, TypedDict

import lizard
from cognitive_complexity.api import get_cognitive_complexity
from radon.metrics import mi_rank, mi_visit

DEFAULT_ROOTS = [
    Path("athetize/scripts"),
    Path("tools"),
    Path("tests"),
]

PYTHON_SUFFIX = ".py"

DEFAULT_EXCLUDE_DIRS = {
    ".venv",
    "__pycache__",
    "dist",
    "build",
}

# Complexity (cyclomatic and cognitive): good up to 10, okay up to 20, bad above.
COMPLEXITY_GOOD_MAX = 10
COMPLEXITY_OKAY_MAX = 20
# Maintainability index: good from 20, okay from 10, bad below.
MI_GOOD_MIN = 20
MI_OKAY_MIN = 10

Table = list[Sequence[str]]


@dataclass(frozen=True)
class FileMetrics:
    """The metrics of one Python file."""

    path: str
    nloc: int
    function_count: int
    cyclomatic_avg: float
    cyclomatic_max: int
    cognitive_avg: float
    cognitive_max: int
    mi: float
    mi_rank: str


class BadFile(TypedDict):
    """A file that rates as bad on at least one metric, and which ones."""

    path: str
    reasons: list[str]
    cyclomatic_max: int
    cognitive_max: int
    mi: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate cyclomatic/cognitive complexity and maintainability index."
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=[str(path) for path in DEFAULT_ROOTS],
        help="Paths to analyze (default: scripts tools tests)",
    )
    parser.add_argument(
        "--exclude-dir",
        nargs="*",
        default=sorted(DEFAULT_EXCLUDE_DIRS),
        help="Directory names to exclude",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top files to show in text output (default: 20)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write JSON output to a file",
    )
    parser.add_argument(
        "--fail-on-bad",
        action="store_true",
        help="Exit non-zero if any file rates as bad for any metric",
    )
    return parser.parse_args()


def is_python_file(path: Path) -> bool:
    return path.suffix.lower() == PYTHON_SUFFIX


def should_skip(path: Path, exclude_dirs: set[str]) -> bool:
    return any(part in exclude_dirs for part in path.parts)


def python_files_under(root: Path, exclude_dirs: set[str]) -> list[Path]:
    """Python files below a directory, leaving out anything inside an excluded directory."""
    return [
        path
        for path in root.rglob("*")
        if not should_skip(path, exclude_dirs) and path.is_file() and is_python_file(path)
    ]


def discover_files(paths: Sequence[str], exclude_dirs: set[str]) -> list[Path]:
    """Python files to analyze for the given files and directories; a missing path is an error."""
    files: list[Path] = []
    for root_str in paths:
        root = Path(root_str)
        if root.is_file():
            if is_python_file(root) and not should_skip(root, exclude_dirs):
                files.append(root)
            continue
        if not root.exists():
            raise FileNotFoundError(f"Path does not exist: {root}")
        files.extend(python_files_under(root, exclude_dirs))
    return sorted(files)


def compute_cognitive(source: str, path: Path) -> list[int]:
    """Cognitive complexity of every function and method in the source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"Failed to parse Python file: {path}") from exc
    return [
        get_cognitive_complexity(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def compute_python_mi(source: str, path: Path) -> tuple[float, str]:
    """Maintainability index of the source and its letter rank (A, B or C)."""
    try:
        score = mi_visit(source, multi=True)
    except SyntaxError as exc:
        raise RuntimeError(f"Failed to compute maintainability index for {path}: {exc}") from exc
    return score, mi_rank(score)


def average(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return statistics.fmean(values)


def build_file_metrics(path: Path) -> FileMetrics:
    source = path.read_text(encoding="utf-8")
    cognitive_values = compute_cognitive(source, path)
    analysis = lizard.analyze_file(str(path))
    cyclomatic_values = [func.cyclomatic_complexity for func in analysis.function_list]
    mi_score, mi_grade = compute_python_mi(source, path)
    return FileMetrics(
        path=str(path),
        nloc=analysis.nloc,
        function_count=len(cyclomatic_values),
        cyclomatic_avg=average(cyclomatic_values),
        cyclomatic_max=max(cyclomatic_values, default=0),
        cognitive_avg=average(cognitive_values),
        cognitive_max=max(cognitive_values, default=0),
        mi=mi_score,
        mi_rank=mi_grade,
    )


def group_key(path: str, roots: Sequence[str]) -> str:
    normalized = path.replace("\\", "/")
    for root in roots:
        root_norm = root.replace("\\", "/").rstrip("/")
        if normalized.startswith(root_norm + "/") or normalized == root_norm:
            return root_norm
    return "other"


def summarize_group(items: list[FileMetrics]) -> dict:
    """Totals, averages and maxima for the files under one path."""
    if not items:
        return {
            "files": 0,
            "nloc": 0,
            "cyclomatic_avg": 0.0,
            "cyclomatic_max": 0,
            "cognitive_avg": 0.0,
            "cognitive_max": 0,
            "mi_avg": None,
        }
    return {
        "files": len(items),
        "nloc": sum(item.nloc for item in items),
        "cyclomatic_avg": average(
            [item.cyclomatic_avg for item in items if item.function_count > 0]
        ),
        "cyclomatic_max": max(item.cyclomatic_max for item in items),
        "cognitive_avg": statistics.fmean(item.cognitive_avg for item in items),
        "cognitive_max": max(item.cognitive_max for item in items),
        "mi_avg": statistics.fmean(item.mi for item in items),
    }


def summarize(metrics: list[FileMetrics], roots: Sequence[str]) -> dict[str, dict]:
    grouped: dict[str, list[FileMetrics]] = {root: [] for root in roots}
    grouped["other"] = []
    for item in metrics:
        grouped[group_key(item.path, roots)].append(item)
    return {key: summarize_group(items) for key, items in grouped.items()}


def to_json(metrics: list[FileMetrics], summary: dict[str, dict]) -> dict:
    return {
        "summary": summary,
        "files": [asdict(item) for item in metrics],
    }


def bad_reasons(item: FileMetrics) -> list[str]:
    """Names of the metrics on which a file rates as bad."""
    ratings = [
        ("cyclomatic", rate_cc(item.cyclomatic_max)),
        ("cognitive", rate_cog(item.cognitive_max)),
        ("maintainability", rate_mi(item.mi)),
    ]
    return [name for name, rating in ratings if rating == "bad"]


def collect_bad_files(metrics: list[FileMetrics]) -> list[BadFile]:
    bad_files: list[BadFile] = []
    for item in metrics:
        reasons = bad_reasons(item)
        if reasons:
            bad_files.append(
                {
                    "path": item.path,
                    "reasons": reasons,
                    "cyclomatic_max": item.cyclomatic_max,
                    "cognitive_max": item.cognitive_max,
                    "mi": item.mi,
                }
            )
    return bad_files


def render_bad_files(bad_files: list[BadFile]) -> str:
    rows: Table = [
        ["File", "Bad metrics", "CC max", "Cog max", "MI"],
    ]
    for item in bad_files:
        rows.append(
            [
                item["path"],
                ", ".join(item["reasons"]),
                str(item["cyclomatic_max"]),
                str(item["cognitive_max"]),
                f"{item['mi']:.2f}",
            ]
        )
    return "\n".join(["", "Bad files (must fix)", format_table(rows)])


def format_table(rows: Table) -> str:
    widths = [max(len(cell) for cell in column) for column in zip(*rows)]
    lines = []
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))
    return "\n".join(lines)


def summary_row(root: str, data: dict) -> list[str]:
    """One line of the summary table for a path."""
    return [
        root,
        str(data["files"]),
        str(data["nloc"]),
        f"{data['cyclomatic_avg']:.2f}",
        str(data["cyclomatic_max"]),
        rate_cc(data["cyclomatic_max"]),
        f"{data['cognitive_avg']:.2f}",
        str(data["cognitive_max"]),
        rate_cog(data["cognitive_max"]),
        f"{data['mi_avg']:.2f}" if data["mi_avg"] is not None else "n/a",
        rate_mi(data["mi_avg"]),
    ]


def render_summary(summary: dict[str, dict], roots: Sequence[str]) -> list[str]:
    """The summary table: one line per path given on the command line."""
    rows: Table = [
        [
            "Path",
            "Files",
            "NLOC",
            "CC avg",
            "CC max",
            "CC rating",
            "Cog avg",
            "Cog max",
            "Cog rating",
            "MI avg",
            "MI rating",
        ]
    ]
    rows.extend(summary_row(root, summary[root]) for root in roots)
    return ["Summary", format_table(rows)]


def render_top_cyclomatic(metrics: list[FileMetrics], top: int) -> list[str]:
    """The files with the highest cyclomatic complexity."""
    rows: Table = [["File", "CC max", "CC rating", "CC avg", "Cog max", "MI"]]
    for item in sorted(metrics, key=lambda item: item.cyclomatic_max, reverse=True)[:top]:
        rows.append(
            [
                item.path,
                str(item.cyclomatic_max),
                rate_cc(item.cyclomatic_max),
                f"{item.cyclomatic_avg:.2f}",
                str(item.cognitive_max),
                f"{item.mi:.2f}",
            ]
        )
    return ["", f"Top {top} files by cyclomatic complexity", format_table(rows)]


def render_top_cognitive(metrics: list[FileMetrics], top: int) -> list[str]:
    """The files with the highest cognitive complexity."""
    rows: Table = [["File", "Cog max", "Cog rating", "Cog avg", "CC max", "MI"]]
    for item in sorted(metrics, key=lambda item: item.cognitive_max, reverse=True)[:top]:
        rows.append(
            [
                item.path,
                str(item.cognitive_max),
                rate_cog(item.cognitive_max),
                f"{item.cognitive_avg:.2f}",
                str(item.cyclomatic_max),
                f"{item.mi:.2f}",
            ]
        )
    return ["", f"Top {top} files by cognitive complexity", format_table(rows)]


def render_lowest_mi(metrics: list[FileMetrics], top: int) -> list[str]:
    """The files with the lowest maintainability index."""
    rows: Table = [["File", "MI", "MI rating", "Rank", "CC max", "Cog max"]]
    for item in sorted(metrics, key=lambda item: item.mi)[:top]:
        rows.append(
            [
                item.path,
                f"{item.mi:.2f}",
                rate_mi(item.mi),
                item.mi_rank,
                str(item.cyclomatic_max),
                str(item.cognitive_max),
            ]
        )
    return ["", f"Lowest {top} MI (Python only)", format_table(rows)]


def render_guidelines() -> list[str]:
    """The table that explains the ratings."""
    rows: Table = [
        ["Metric", "Good", "Okay", "Bad"],
        ["Cyclomatic (CC)", "1-10", "11-20", "21+"],
        ["Cognitive (Cog)", "0-10", "11-20", "21+"],
        ["Maintainability (MI)", ">= 20 (A)", "10-19 (B)", "< 10 (C)"],
    ]
    return ["", "Guidelines", format_table(rows)]


def render_text(
    metrics: list[FileMetrics],
    summary: dict[str, dict],
    roots: Sequence[str],
    top: int,
) -> str:
    lines = render_summary(summary, roots)
    if metrics:
        lines += render_top_cyclomatic(metrics, top)
        lines += render_top_cognitive(metrics, top)
        lines += render_lowest_mi(metrics, top)
    lines += render_guidelines()
    return "\n".join(lines)


def rate_complexity(value: int) -> str:
    """Rating shared by cyclomatic and cognitive complexity: lower is better."""
    if value <= COMPLEXITY_GOOD_MAX:
        return "good"
    if value <= COMPLEXITY_OKAY_MAX:
        return "okay"
    return "bad"


def rate_cc(value: int) -> str:
    return rate_complexity(value)


def rate_cog(value: int) -> str:
    return rate_complexity(value)


def rate_mi(value: Optional[float]) -> str:
    """Rating of a maintainability index: higher is better; no files means no rating."""
    if value is None:
        return "n/a"
    if value >= MI_GOOD_MIN:
        return "good"
    if value >= MI_OKAY_MIN:
        return "okay"
    return "bad"


def render_json(
    metrics: list[FileMetrics], summary: dict[str, dict], bad_files: list[BadFile]
) -> str:
    """The full results as a JSON document."""
    payload = to_json(metrics, summary)
    payload["bad_files"] = bad_files
    payload["has_bad_files"] = bool(bad_files)
    return json.dumps(payload, indent=2)


def main() -> int:
    args = parse_args()
    files = discover_files(args.paths, set(args.exclude_dir))
    metrics = [build_file_metrics(path) for path in files]
    summary = summarize(metrics, args.paths)
    bad_files = collect_bad_files(metrics)

    if args.output:
        Path(args.output).write_text(render_json(metrics, summary, bad_files), encoding="utf-8")
    elif args.format == "json":
        print(render_json(metrics, summary, bad_files))
    else:
        report = render_text(metrics, summary, args.paths, args.top)
        if bad_files:
            report = f"{report}{render_bad_files(bad_files)}"
        print(report)

    if args.fail_on_bad and bad_files:
        print(
            f"Found {len(bad_files)} file(s) with bad metrics.",
            file=sys.stderr,
        )
        return 1
    return 0


def run() -> int:
    try:
        return main()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
