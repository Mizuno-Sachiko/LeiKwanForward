"""Install LeiKwanForward and its system command on Linux."""

import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description="安装LeiKwanForward。")
parser.add_argument(
    "--install-dir",
    type=Path,
    default=Path("/opt/LeiKwanForward"),
    metavar="DIR",
    help="安装目录；默认/opt/LeiKwanForward",
)
parser.add_argument("--dry-run", action="store_true", help="预览安装")
args = parser.parse_args()

try:
    if sys.platform != "linux" or sys.version_info < (3, 12):
        raise ValueError("需要Linux及Python3.12或以上。")
    if not args.install_dir.is_absolute():
        raise ValueError("安装目录必须是绝对路径。")
    target = args.install_dir.resolve()
    source = Path(__file__).resolve().parent
    if target == Path(target.anchor):
        raise ValueError("安装目录不能是文件系统根目录。")
    names = (
        "LeiKwanForward.py",
        "leikwan_api.py",
        "README.md",
        "test_leikwan_api.py",
        "install-linux-forward.py",
    )
    for name in names:
        if not (source / name).is_file():
            raise ValueError(f"安装包缺少{name}。")
    if target.exists() and (
        not target.is_dir()
        or (
            any(target.iterdir())
            and not all(
                (target / name).is_file()
                for name in ("LeiKwanForward.py", "leikwan_api.py")
            )
        )
    ):
        raise ValueError("目标目录非空且不是LeiKwanForward安装。")
    launcher = Path("/usr/local/bin/LeiKwanForward")
    if launcher.exists() and "LeiKwanForward.py" not in launcher.read_text(
        encoding="utf-8"
    ):
        raise ValueError(f"{launcher}已被其他程序使用。")
    if args.dry_run:
        print(
            f"install: {target}\ncommand: {launcher}\ncredentials: {target / 'data/api-token'}"
        )
        sys.exit(0)
    if os.geteuid() != 0:
        raise ValueError("需要root权限；请通过sudo执行安装脚本。")

    target.mkdir(parents=True, exist_ok=True)
    os.chown(target, 0, 0)
    target.chmod(0o755)
    for name in names:
        file = target / name
        if target != source:
            shutil.copyfile(source / name, file)
        os.chown(file, 0, 0)
        file.chmod(0o644)

    # Keep credentials writable by the user who invoked sudo, outside program files.
    data = target / "data"
    if not data.exists():
        data.mkdir(mode=0o700)
        os.chown(
            data,
            int(os.environ.get("SUDO_UID", "0")),
            int(os.environ.get("SUDO_GID", "0")),
        )
    data.chmod(0o700)

    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -B "
        f'{shlex.quote(str(target / "LeiKwanForward.py"))} "$@"\n',
        encoding="utf-8",
    )
    os.chown(launcher, 0, 0)
    launcher.chmod(0o755)
    print(f"installed: {target}", file=sys.stderr)
except (OSError, ValueError) as error:
    print(f"错误：{error}", file=sys.stderr)
    sys.exit(1)
