"""汇总 W1 评估 CSV（由前端"导出评估 CSV"产生），输出 Top-1/Top-3/延迟分位。

用法：
    python scripts/summarize_eval.py reports/eval/*.csv
    python scripts/summarize_eval.py reports/eval/a.csv --out reports/eval_summary.md
"""

from __future__ import annotations

import argparse
import csv
import glob
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def pct(values: list[float], p: float) -> float:
    return float(np.percentile(values, p)) if values else 0.0


def load_rows(patterns: list[str]) -> list[dict]:
    paths: list[str] = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern) if any(ch in pattern for ch in "*?[") else [pattern])
    rows: list[dict] = []
    for path in sorted(set(paths)):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row["_file"] = Path(path).name
                rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if (r.get("expected") or "").strip()]
    top1 = [r for r in scored if r["text"] == r["expected"]]
    top3 = [r for r in scored if r["expected"] in (r.get("topk") or "").split("|")]
    e2e = [float(r["e2e_ms"]) for r in rows if str(r.get("e2e_ms", "")).strip()]
    match = [float(r["match_ms"]) for r in rows if str(r.get("match_ms", "")).strip()]
    confusions = Counter((r["expected"], r["text"]) for r in scored if r["text"] != r["expected"])
    return {
        "total": len(rows),
        "scored": len(scored),
        "top1": len(top1) / len(scored) if scored else 0.0,
        "top3": len(top3) / len(scored) if scored else 0.0,
        "e2e_p50": pct(e2e, 50), "e2e_p95": pct(e2e, 95),
        "match_p50": pct(match, 50), "match_p95": pct(match, 95),
        "confusions": confusions.most_common(10),
    }


GATES = {"top1": 0.80, "top3": 0.95, "e2e_p95": 250.0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", nargs="+", help="CSV 路径或通配符")
    parser.add_argument("--out", default=str(ROOT / "reports" / "eval_summary.md"))
    args = parser.parse_args()

    rows = load_rows(args.csv)
    if not rows:
        print("没有找到可汇总的记录")
        return 1
    s = summarize(rows)
    lines = [
        "# W1 评估汇总",
        "",
        f"- 样本总数：{s['total']}（其中带期望词 {s['scored']}）",
        f"- Top-1：**{s['top1']*100:.1f}%**（门禁 ≥ {GATES['top1']*100:.0f}%）"
        f" {'PASS' if s['top1'] >= GATES['top1'] else 'FAIL'}",
        f"- Top-3：**{s['top3']*100:.1f}%**（门禁 ≥ {GATES['top3']*100:.0f}%）"
        f" {'PASS' if s['top3'] >= GATES['top3'] else 'FAIL'}",
        f"- L1 端到端：P50 {s['e2e_p50']:.0f}ms / P95 {s['e2e_p95']:.0f}ms"
        f"（门禁 P95 ≤ {GATES['e2e_p95']:.0f}ms） {'PASS' if s['e2e_p95'] <= GATES['e2e_p95'] else 'FAIL'}",
        f"- 端侧匹配：P50 {s['match_p50']:.1f}ms / P95 {s['match_p95']:.1f}ms",
        "",
        "## 主要混淆",
        "",
        "| 期望 | 识别 | 次数 |",
        "|---|---|---:|",
    ]
    if s["confusions"]:
        for (expected, got), count in s["confusions"]:
            lines.append(f"| {expected} | {got} | {count} |")
    else:
        lines.append("| — | — | — |")
    lines.append("")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
