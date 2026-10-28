"""检查 Vite 生产构建的 JavaScript 体积预算并生成 JSON 报告。"""

import argparse
import json
from pathlib import Path
from typing import Any


def analyze_bundle(dist_dir: Path) -> dict[str, Any]:
    """
    汇总生产构建中的 JavaScript 文件大小。

    Args:
        dist_dir: Vite 生成的 dist 目录。

    Returns:
        总体积、最大 chunk 和逐文件体积。

    Raises:
        RuntimeError: 构建目录或 JavaScript 产物不存在时抛出。
    """
    asset_dir = dist_dir / "assets"
    chunks = sorted(asset_dir.glob("*.js"))
    if not chunks:
        raise RuntimeError(f"未找到Vite JavaScript产物: {asset_dir}")
    files = [{"name": path.name, "bytes": path.stat().st_size} for path in chunks]
    largest = max(files, key=lambda item: item["bytes"])
    return {
        "total_js_bytes": sum(item["bytes"] for item in files),
        "largest_chunk": largest,
        "files": files,
    }


def evaluate_budget(
    bundle: dict[str, Any],
    max_chunk_bytes: int,
    max_total_bytes: int,
) -> dict[str, Any]:
    """
    将构建统计与发布预算比较。

    Args:
        bundle: analyze_bundle 返回的构建统计。
        max_chunk_bytes: 单个 JavaScript chunk 最大字节数。
        max_total_bytes: 全部 JavaScript 最大字节数。

    Returns:
        包含阈值、是否通过和超限原因的报告。
    """
    failures = []
    if bundle["largest_chunk"]["bytes"] > max_chunk_bytes:
        failures.append("largest_chunk")
    if bundle["total_js_bytes"] > max_total_bytes:
        failures.append("total_js")
    return {
        **bundle,
        "max_chunk_bytes": max_chunk_bytes,
        "max_total_bytes": max_total_bytes,
        "passed": not failures,
        "failures": failures,
    }


def parse_args() -> argparse.Namespace:
    """
    解析构建目录、预算和报告输出路径。

    Returns:
        前端体积门禁命令参数。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--max-chunk-bytes", type=int, default=1_300_000)
    parser.add_argument("--max-total-bytes", type=int, default=2_200_000)
    parser.add_argument("--report", type=Path, default=Path("reports/frontend-bundle.json"))
    return parser.parse_args()


def main() -> int:
    """
    执行体积门禁并返回适合 CI 的退出码。

    Returns:
        未超过预算时返回 0，否则返回 1。
    """
    args = parse_args()
    report = evaluate_budget(
        analyze_bundle(args.dist),
        args.max_chunk_bytes,
        args.max_total_bytes,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(output + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
