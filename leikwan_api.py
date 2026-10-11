"""LeiKwan v1 forward API and installation-local token storage."""

import ctypes
import json
import sys
from ctypes import wintypes
from email.message import Message
from http.client import HTTPException
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Self
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PANEL = "https://v3.leikwanhost.com"
# Keep credentials with the selected installation, independent of the shell's cwd.
TOKEN_FILE = Path(__file__).resolve().parent / "data" / "api-token"


class APIError(Exception):
    """A request failure retaining the HTTP status, headers, body and decoded JSON."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        headers: Message | None = None,
        body: str | None = None,
        result: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Keep native header lookup case-insensitive and preserve repeated fields.
        self.headers = headers if headers is not None else Message()
        self.body = body
        self.result = result


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def crypt_token(data: bytes, *, decrypt: bool = False) -> bytes:
    """Protect the token for the current Windows user, without displaying UI."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    output = DataBlob()
    # Omit LOCAL_MACHINE to keep protection scoped to the current user.
    if not function(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def save_token(token: str) -> None:
    """Store credentials with Windows encryption or Linux owner-only permissions."""
    content = token.encode("utf-8")
    TOKEN_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if sys.platform == "win32":
        content = crypt_token(content)
    else:
        TOKEN_FILE.parent.chmod(0o700)
    # Stage in the protected directory; close successfully before replacing the old token.
    with NamedTemporaryFile(
        dir=TOKEN_FILE.parent, prefix=".api-token-", delete_on_close=False
    ) as output:
        output.write(content)
        output.close()
        Path(output.name).replace(TOKEN_FILE)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # Authenticated API requests must never be redirected to another host.
        return None


class Client:
    def __init__(self, token: str) -> None:
        if not token or any(character.isspace() for character in token):
            raise ValueError("API令牌不能为空或包含空白字符。")
        self.token = token
        self.opener = build_opener(NoRedirect())

    @classmethod
    def from_saved_token(cls) -> Self:
        if not TOKEN_FILE.is_file():
            raise ValueError("尚未保存API令牌，请执行LeiKwanForward login。")
        content = TOKEN_FILE.read_bytes()
        if sys.platform == "win32":
            content = crypt_token(content, decrypt=True)
        return cls(content.decode("utf-8"))

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        etag: str | None = None,
        key: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        # Identify the application; the panel filters generic runtime signatures.
        headers = {"Accept": "application/json", "User-Agent": "LeiKwanForward"}
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            if len(body) > 16 * 1024:
                raise ValueError("请求体不能超过16KiB。")
            headers["Content-Type"] = "application/json"
        if etag is not None:
            headers["If-Match"] = etag
        if key is not None:
            headers["Idempotency-Key"] = key
        request = Request(f"{PANEL}/api/v1/user/{path}", body, headers, method=method)
        request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        try:
            try:
                response = self.opener.open(request, timeout=30)
            except HTTPError as error:
                # Read error bodies under the same transport exception handling.
                response = error
            with response:
                if response.status == 204:
                    return {}, None
                # Preserve the body even when it is HTML or an undocumented JSON shape.
                response_body = response.read().decode("utf-8", errors="replace")
                result = None
                try:
                    result = json.loads(response_body)
                except json.JSONDecodeError:
                    pass
                if (
                    isinstance(response, HTTPError)
                    or not isinstance(result, dict)
                    or result.get("ok") is not True
                ):
                    raise APIError(
                        f"HTTP {response.status}",
                        status=response.status,
                        headers=response.headers,
                        body=response_body,
                        result=result,
                    ) from None
                return result, response.headers.get("ETag")
        except (OSError, HTTPException) as error:
            # Never retry uncertain writes; POST must reuse its original key.
            message = f"未收到完整请求结果：{error}。"
            if method != "GET":
                message += "写操作结果尚不确定，请查询转发状态后再操作。"
            raise APIError(message) from None
