"""需要直接访问部署数据库的安全运维命令。"""

import argparse
from typing import Optional, Sequence

from .services.auth_service import AuthenticationError
from .services.platform_services import get_auth_service


def build_parser() -> argparse.ArgumentParser:
    """
    构造应用运维命令解析器。

    Returns:
        包含管理员提升子命令的 argparse 解析器。
    """
    parser = argparse.ArgumentParser(description="Hili Trip 运维命令")
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser(
        "promote-admin",
        help="将一个已注册账号提升为管理员",
    )
    promote.add_argument("email", help="已注册账号邮箱")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    执行指定运维命令并返回进程退出码。

    Args:
        argv: 测试可传入的参数列表；None 时读取命令行。

    Returns:
        成功返回 0，业务校验失败返回 1。
    """
    args = build_parser().parse_args(argv)
    if args.command == "promote-admin":
        try:
            user = get_auth_service().promote_admin(args.email)
        except AuthenticationError as exc:
            print(f"管理员提升失败: {exc}")
            return 1
        print(f"已授予管理员权限: {user.email}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
