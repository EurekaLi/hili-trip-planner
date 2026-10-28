"""对已启动的 Compose 栈执行无付费 HTTP 冒烟检查。"""

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class CheckResult:
    """单个 HTTP 冒烟检查结果。"""

    name: str
    url: str
    status_code: int
    elapsed_ms: float
    passed: bool
    detail: str


def fetch(url: str, expected_status: int, expected_text: str) -> CheckResult:
    """
    请求一个部署端点并验证状态码和响应片段。

    Args:
        url: 需要检查的绝对 URL。
        expected_status: 预期 HTTP 状态码。
        expected_text: 响应正文必须包含的文本。

    Returns:
        包含耗时和失败原因的检查结果。
    """
    started = time.perf_counter()
    status_code = 0
    detail = ""
    try:
        request = Request(url, headers={"User-Agent": "hili-trip-smoke/1.0"})
        with urlopen(request, timeout=5) as response:
            status_code = response.status
            body = response.read().decode("utf-8", errors="replace")
        passed = status_code == expected_status and expected_text in body
        if not passed:
            detail = f"expected status={expected_status} and text={expected_text!r}"
    except HTTPError as exc:
        status_code = exc.code
        passed = False
        detail = f"HTTPError: {exc.reason}"
    except (TimeoutError, URLError) as exc:
        passed = False
        detail = f"{type(exc).__name__}: {exc}"
    return CheckResult(
        name=url.rsplit("/", 1)[-1] or "frontend",
        url=url,
        status_code=status_code,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        passed=passed,
        detail=detail,
    )


def wait_for_check(
    url: str,
    expected_status: int,
    expected_text: str,
    timeout_seconds: float,
) -> CheckResult:
    """
    在部署冷启动窗口内重试检查，返回最后一次结果。

    Args:
        url: 需要检查的绝对 URL。
        expected_status: 预期 HTTP 状态码。
        expected_text: 响应正文必须包含的文本。
        timeout_seconds: 最长等待秒数。

    Returns:
        首次成功或超时前最后一次检查结果。
    """
    deadline = time.monotonic() + timeout_seconds
    result = fetch(url, expected_status, expected_text)
    while not result.passed and time.monotonic() < deadline:
        time.sleep(2)
        result = fetch(url, expected_status, expected_text)
    return result


def build_report(base_url: str, timeout_seconds: float) -> dict[str, Any]:
    """
    检查前端入口、存活探针和就绪探针。

    Args:
        base_url: Nginx 对外暴露的站点地址。
        timeout_seconds: 每个检查允许的冷启动等待时间。

    Returns:
        可写入 JSON 的部署冒烟报告。
    """
    normalized = base_url.rstrip("/")
    checks = [
        wait_for_check(normalized, 200, "<div id=\"app\"></div>", timeout_seconds),
        wait_for_check(
            f"{normalized}/health/live",
            200,
            '"status":"alive"',
            timeout_seconds,
        ),
        wait_for_check(
            f"{normalized}/health/ready",
            200,
            '"ready":true',
            timeout_seconds,
        ),
    ]
    return {
        "passed": all(check.passed for check in checks),
        "base_url": normalized,
        "checks": [asdict(check) for check in checks],
    }


def parse_args() -> argparse.Namespace:
    """
    解析冒烟测试命令行参数。

    Returns:
        基础地址、超时和报告路径。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    """
    执行部署冒烟并按结果返回进程退出码。

    Returns:
        全部检查通过时为 0，否则为 1。
    """
    args = parse_args()
    report = build_report(args.base_url, args.timeout)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
