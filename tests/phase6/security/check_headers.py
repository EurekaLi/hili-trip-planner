"""验证生产 Web 入口的最低安全响应头。"""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(self)",
}


def check_headers(base_url: str) -> dict[str, object]:
    """
    请求站点首页并校验关键浏览器安全响应头。

    Args:
        base_url: Nginx 对外暴露的站点地址。

    Returns:
        可序列化的实际响应头、缺失项和通过状态。
    """
    request = Request(base_url.rstrip("/") + "/", method="GET")
    with urlopen(request, timeout=5) as response:
        actual = {key.lower(): value for key, value in response.headers.items()}
    missing = {
        name: expected
        for name, expected in EXPECTED_HEADERS.items()
        if expected not in actual.get(name, "")
    }
    return {
        "passed": not missing,
        "expected": EXPECTED_HEADERS,
        "actual": {name: actual.get(name) for name in EXPECTED_HEADERS},
        "missing_or_invalid": missing,
    }


def parse_args() -> argparse.Namespace:
    """
    解析站点地址和报告输出路径。

    Returns:
        安全头检查命令参数。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    """
    执行安全头检查并返回 CI 退出码。

    Returns:
        全部响应头满足要求时返回 0，否则返回 1。
    """
    args = parse_args()
    report = check_headers(args.base_url)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
