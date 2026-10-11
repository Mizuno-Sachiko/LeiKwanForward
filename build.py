"""Build the Windows and Linux LeiKwanForward installation packages."""

import argparse
import hashlib
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime-archive", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, default=root / "dist")
args = parser.parse_args()
runtime_archive = args.runtime_archive
# SHA256 from https://www.python.org/ftp/python/3.14.8/windows-3.14.8.json
expected = "80292f0e640e373a54bf09f3b94a1472f976f24a10c5bed0d9622e4e44d67447"
with runtime_archive.open("rb") as source:
    if hashlib.file_digest(source, "sha256").hexdigest() != expected:
        parser.error("Python runtime archive does not match the official SHA256.")

common = ("LeiKwanForward.py", "leikwan_api.py", "README.md", "test_leikwan_api.py")
windows = args.output_dir / "LeiKwanForward-windows-x64.zip"
linux = args.output_dir / "LeiKwanForward-linux.tar.gz"
for path in (windows, linux):
    if path.exists() or Path(str(path) + ".sha256").exists():
        parser.error(f"Output already exists: {path}")
args.output_dir.mkdir(parents=True, exist_ok=True)

# Explicit file lists keep installed credentials and local artifacts out of both packages.
with (
    ZipFile(runtime_archive) as runtime,
    ZipFile(windows, "x", compression=ZIP_DEFLATED) as archive,
):
    for name in (*common, "LeiKwanForward.cmd", "install-windows-forward.ps1"):
        archive.write(root / name, f"LeiKwanForward/{name}")
    for name in runtime.namelist():
        content = runtime.read(name)
        if name == "python314._pth":
            # Limit imports to the bundled library and application directory.
            content = b"python314.zip\n.\n..\n"
        archive.writestr(f"LeiKwanForward/runtime/{name}", content)

with tarfile.open(linux, "x:gz") as archive:
    for name in (*common, "install-linux-forward.py"):
        path = root / name
        info = archive.gettarinfo(path, f"LeiKwanForward/{name}")
        info.mode = 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        with path.open("rb") as source:
            archive.addfile(info, source)

for path in (windows, linux):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with Path(str(path) + ".sha256").open("x", encoding="utf-8") as checksum:
        checksum.write(f"{digest}  {path.name}\n")
    print(path)
