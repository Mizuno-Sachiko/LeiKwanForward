"""Command-line management of LeiKwan v1 forward rules."""

import argparse
import json
import re
import sys
import warnings
from getpass import GetPassWarning, getpass
from typing import Any
from uuid import uuid4

from leikwan_api import TOKEN_FILE, APIError, Client, save_token


def identifier(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("ID必须是正整数。")
    return number


def port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("端口范围是1–65535。")
    return number


def listen_port(value: str) -> int | None:
    return None if value == "auto" else port(value)


def idempotency_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._:-]{8,64}", value) is None:
        raise argparse.ArgumentTypeError("创建请求标识须为8–64个字母、数字或._:-字符。")
    return value


def show(data: dict[str, Any], *, preview: bool = False) -> None:
    # Keep previews out of the result stream used by other programs.
    print(
        json.dumps(data, ensure_ascii=False, indent=2),
        file=sys.stderr if preview else sys.stdout,
    )


def confirm(message: str, *, yes: bool = False) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise ValueError("需要交互确认；自动化调用请指定-y或--yes。")
    print(f"{message} [y/N] ", end="", file=sys.stderr, flush=True)
    return input().strip().lower() in {"y", "yes"}


parser = argparse.ArgumentParser(
    prog="LeiKwanForward",
    description="管理会聚专线转发。",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
        "示例（ID请按实际填写）：\n"
        '  LeiKwanForward login "YOUR_API_TOKEN"\n'
        "  LeiKwanForward services\n"
        "  LeiKwanForward status --service 42\n"
        "  LeiKwanForward list --service 42\n"
        "  LeiKwanForward edit 123 --port 8443\n\n"
        "查看子命令帮助：LeiKwanForward <command> --help"
    ),
)
commands = parser.add_subparsers(
    dest="command", title="commands", metavar="<command>", required=True
)
command = commands.add_parser(
    "login",
    help="保存API令牌",
    description="保存API令牌。",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
        "示例：\n"
        '  LeiKwanForward login "YOUR_API_TOKEN"\n'
        "  LeiKwanForward login\n\n"
        "传入令牌时免交互保存。"
    ),
)
command.add_argument(
    "token", nargs="?", metavar="TOKEN", help="API令牌；省略时隐藏输入"
)
command = commands.add_parser(
    "logout",
    help="移除本地令牌",
    description="移除本地令牌。",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="示例：\n  LeiKwanForward logout\n  LeiKwanForward logout -y",
)
command.add_argument("-y", "--yes", action="store_true", help="跳过确认")
command = commands.add_parser(
    "services",
    help="列出服务",
    description="列出服务。",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="示例：\n  LeiKwanForward services",
)
for name, description in [
    ("status", "检查认证和服务权限"),
    ("routes", "查询线路和配额"),
    ("list", "列出转发"),
]:
    command = commands.add_parser(
        name,
        help=description,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"示例（ID请按实际填写）：\n  LeiKwanForward {name} --service 42",
    )
    command.add_argument(
        "--service", type=identifier, required=True, metavar="ID", help="服务ID"
    )
command = commands.add_parser(
    "show",
    help="查看转发",
    description="查看转发。",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="示例（ID请按实际填写）：\n  LeiKwanForward show 123",
)
command.add_argument("id", type=identifier, metavar="ID", help="转发ID")
for name, description in [("create", "创建转发"), ("edit", "修改转发")]:
    command = commands.add_parser(
        name,
        help=description,
        description=description,
        argument_default=argparse.SUPPRESS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if name == "create":
        command.epilog = (
            "示例（ID请按实际填写）：\n"
            "  LeiKwanForward create --service 42 --route 14 "
            "--host 203.0.113.10 --port 443\n"
            "  LeiKwanForward create --service 42 --route 14 --native-outbound\n\n"
            "省略--listen时自动分配端口。"
        )
        command.add_argument(
            "--service", type=identifier, required=True, metavar="ID", help="服务ID"
        )
        command.add_argument(
            "--key",
            type=idempotency_key,
            metavar="KEY",
            help="创建请求标识；默认自动生成",
        )
        command.set_defaults(listen=None, transport="tcp+udp", native_outbound=False)
        command.add_argument(
            "--native-outbound", action="store_true", help="设备侧出站转发"
        )
    else:
        command.epilog = (
            "示例（ID请按实际填写）：\n"
            "  LeiKwanForward edit 123 --host backend.example.com --port 8443\n"
            "  LeiKwanForward edit 123 --route 14 --listen auto\n\n"
            "只修改指定字段；修改会重建连接。"
        )
        command.add_argument("id", type=identifier, metavar="ID", help="转发ID")
        native = command.add_mutually_exclusive_group()
        native.add_argument(
            "--native-outbound",
            action="store_true",
            help="启用设备侧出站；需指定--transport tcp+udp",
        )
        native.add_argument(
            "--no-native-outbound",
            dest="native_outbound",
            action="store_false",
            help="关闭设备侧出站；需指定目标及端口",
        )
    command.add_argument(
        "--route",
        type=identifier,
        required=name == "create",
        metavar="ID",
        help="线路ID",
    )
    command.add_argument(
        "--listen", type=listen_port, metavar="PORT", help="监听端口；auto自动分配"
    )
    command.add_argument(
        "--transport",
        choices=["tcp", "udp", "tcp+udp"],
        help="传输方式；默认tcp+udp" if name == "create" else "传输方式",
    )
    target = command.add_mutually_exclusive_group()
    target.add_argument("--host", metavar="HOST", help="目标IP或域名")
    target.add_argument(
        "--target-service", type=identifier, metavar="ID", help="目标服务ID"
    )
    command.add_argument("--port", type=port, metavar="PORT", help="目标端口")
    command.add_argument(
        "-y", "--yes", action="store_true", default=False, help="跳过确认"
    )
command = commands.add_parser(
    "delete",
    help="删除转发",
    description="删除转发。",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="示例（ID请按实际填写）：\n  LeiKwanForward delete 123\n  LeiKwanForward delete 123 -y",
)
command.add_argument("id", type=identifier, metavar="ID", help="转发ID")
command.add_argument("-y", "--yes", action="store_true", help="跳过确认")
if len(sys.argv) == 1:
    # Keep the default overview short; explicit help includes examples.
    parser.epilog = None
args = parser.parse_args(None if len(sys.argv) > 1 else ["--help"])
options = vars(args)

try:
    if args.command == "login":
        if args.token is not None:
            # An explicit token requests non-interactive credential replacement.
            token = args.token.strip()
        else:
            if TOKEN_FILE.exists() and not confirm("覆盖已保存的API令牌？"):
                sys.exit(0)
            with warnings.catch_warnings():
                warnings.simplefilter("error", GetPassWarning)
                token = getpass("专线API令牌（输入不显示）：").strip()
        Client(token)
        save_token(token)
    elif args.command == "logout":
        if TOKEN_FILE.exists() and confirm("移除本地API令牌？", yes=args.yes):
            TOKEN_FILE.unlink()
    else:
        data = {}
        if args.command in {"create", "edit"}:
            fields = {
                "route": "route_id",
                "listen": "listen_port",
                "transport": "transport",
                "native_outbound": "native_outbound",
            }
            data = {
                field: options[option]
                for option, field in fields.items()
                if option in options
            }
            target_data = {}
            if "host" in options:
                target_data = {"source": "manual", "host": args.host}
            elif "target_service" in options:
                target_data = {
                    "source": "service_ip",
                    "service_id": args.target_service,
                }
            if "port" in options:
                target_data["port"] = args.port
            if target_data:
                data["target"] = target_data
            if data.get("native_outbound") is True:
                # Native targets are assigned by the platform, including on conversion.
                if target_data or data.get("transport") != "tcp+udp":
                    parser.error("设备侧出站要求tcp+udp，且不能指定目标。")
            elif args.command == "create":
                if "source" not in target_data or "port" not in target_data:
                    parser.error(
                        "创建转发需指定--host或--target-service，并指定--port。"
                    )
            elif not data:
                parser.error("修改转发需至少指定一个字段。")
            elif data.get("native_outbound") is False and (
                "source" not in target_data or "port" not in target_data
            ):
                parser.error("关闭设备侧出站需同时指定目标和--port。")

        client = Client.from_saved_token()
        if args.command == "services":
            result, _ = client.request("GET", "services")
            show(result)
        elif args.command in {"status", "routes", "list"}:
            suffix = "forwards" if args.command == "list" else "forward-options"
            result, _ = client.request("GET", f"services/{args.service}/{suffix}")
            if args.command == "status":
                show({"ok": True, "authenticated": True, "service_id": args.service})
            else:
                show(result)
        elif args.command == "create":
            key = options.get("key", uuid4().hex)
            path = f"services/{args.service}/forwards"
            if not args.yes:
                show(data, preview=True)
            if confirm(f"在服务{args.service}中创建转发？", yes=args.yes):
                try:
                    result, _ = client.request("POST", path, data, key=key)
                except KeyboardInterrupt:
                    # Interrupting a POST does not cancel creation on the platform.
                    print(
                        f"\n创建结果尚不确定。\nIdempotency-Key: {key}", file=sys.stderr
                    )
                    sys.exit(130)
                except APIError:
                    # Keep the original response and the key needed for a creation retry.
                    print(f"Idempotency-Key: {key}", file=sys.stderr)
                    raise
                show(result)
        else:
            path = f"forwards/{args.id}"
            current, etag = client.request("GET", path)
            if args.command == "show":
                show(current)
            else:
                if etag is None:
                    raise APIError("平台没有返回ETag，无法执行修改或删除。")
                if not args.yes:
                    show(data if args.command == "edit" else current, preview=True)
                if args.command == "edit":
                    if confirm(f"修改转发{args.id}并重建连接？", yes=args.yes):
                        result, _ = client.request("PATCH", path, data, etag=etag)
                        show(result)
                elif confirm(f"删除转发{args.id}？", yes=args.yes):
                    client.request("DELETE", path, etag=etag)
                    show({"ok": True, "deleted_id": args.id})
except (APIError, ValueError, OSError, EOFError, GetPassWarning) as error:
    if isinstance(error, APIError) and error.body:
        # Keep the platform body separate from diagnostics and retry information.
        print(error.body, end="" if error.body.endswith("\n") else "\n")
    print(f"{parser.prog}: {error}", file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    print("\n已取消。", file=sys.stderr)
    sys.exit(130)
