"""Offline checks; API calls are mocked and no token is stored."""

import io
import json
import runpy
import sys
import unittest
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import leikwan_api
from leikwan_api import APIError, Client, NoRedirect, crypt_token, save_token


class APIProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Client("synthetic-api-token")
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.status = 200
        self.response.headers = Message()
        self.response.headers["ETag"] = '"forward-123-r3"'
        self.response.read.return_value = b'{"ok": true}'
        self.client.opener = MagicMock()
        self.client.opener.open.return_value = self.response

    def test_json_headers_and_etag(self) -> None:
        result, etag = self.client.request(
            "PATCH", "forwards/123", {"listen_port": 20018}, etag='"forward-123-r2"'
        )
        request = self.client.opener.open.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"), "Bearer synthetic-api-token"
        )
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.get_header("User-agent"), "LeiKwanForward")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("If-match"), '"forward-123-r2"')
        self.assertEqual(json.loads(request.data), {"listen_port": 20018})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(etag, '"forward-123-r3"')

    def test_creation_key(self) -> None:
        self.client.request(
            "POST", "services/42/forwards", {}, key="synthetic-create-01"
        )
        request = self.client.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Idempotency-key"), "synthetic-create-01")

    def test_delete_empty_response(self) -> None:
        self.response.status = 204
        self.assertEqual(self.client.request("DELETE", "forwards/123"), ({}, None))
        self.response.read.assert_not_called()

    def test_platform_error(self) -> None:
        result = {
            "ok": False,
            "error": {
                "code": "forward_version_conflict",
                "message": "Version changed.",
            },
            "request_id": "synthetic-request-01",
        }
        body = json.dumps(result, indent=2)
        self.client.opener.open.side_effect = HTTPError(
            "https://v3.leikwanhost.com/api/v1/user/forwards/123",
            412,
            "Precondition Failed",
            {},
            io.BytesIO(body.encode()),
        )
        with self.assertRaisesRegex(APIError, "HTTP 412") as raised:
            self.client.request("PATCH", "forwards/123", {})
        error = raised.exception
        self.assertEqual(error.status, 412)
        self.assertEqual(error.body, body)
        self.assertEqual(error.result, result)
        self.assertEqual(str(error), "HTTP 412")
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_error_headers_preserve_lookup_and_repeated_fields(self) -> None:
        for status, code, name, value in (
            (429, "rate_limited", "retry-after", "60"),
            (405, "method_not_allowed", "allow", "GET, POST"),
        ):
            with self.subTest(status=status):
                headers = Message()
                headers[name] = value
                headers["X-Test"] = "first"
                headers["X-Test"] = "second"
                result = {"ok": False, "error": {"code": code}}
                body = json.dumps(result)
                self.client.opener.open.side_effect = HTTPError(
                    "https://v3.leikwanhost.com/api/v1/user/services",
                    status,
                    code,
                    headers,
                    io.BytesIO(body.encode()),
                )
                with self.assertRaises(APIError) as raised:
                    self.client.request("GET", "services")
                error = raised.exception
                self.assertEqual(error.status, status)
                self.assertIs(error.headers, headers)
                self.assertEqual(error.headers.get(name.upper()), value)
                self.assertEqual(error.headers.get_all("x-test"), ["first", "second"])
                self.assertEqual(error.body, body)
                self.assertEqual(error.result, result)
                self.assertEqual(str(error), f"HTTP {status}")

    def test_cloudflare_error(self) -> None:
        body = {
            "cloudflare_error": True,
            "error_code": 1010,
            "detail": "Access denied based on the client signature.",
        }
        self.client.opener.open.side_effect = HTTPError(
            "https://v3.leikwanhost.com/api/v1/user/services/42/forwards",
            403,
            "Forbidden",
            {},
            io.BytesIO(json.dumps(body).encode()),
        )
        with self.assertRaisesRegex(APIError, "HTTP 403") as raised:
            self.client.request("GET", "services/42/forwards")
        self.assertEqual(raised.exception.result, body)
        self.assertEqual(json.loads(raised.exception.body), body)
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_undocumented_json_errors_are_preserved(self) -> None:
        for result in (
            {"error": "not_found", "path": "/api/user/services/42/forwards"},
            {"ok": False, "error": {"code": "validation_failed"}},
            {
                "ok": False,
                "error": {"code": "unknown_fields", "fields": ["target.foo"]},
            },
            ["unexpected", "response"],
            "unexpected response",
            None,
        ):
            with self.subTest(result=result):
                body = json.dumps(result, ensure_ascii=False, indent=2)
                self.client.opener.open.side_effect = HTTPError(
                    "https://v3.leikwanhost.com/api/v1/user/services/42/forwards",
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(body.encode()),
                )
                with self.assertRaises(APIError) as raised:
                    self.client.request("GET", "services/42/forwards")
                self.assertEqual(raised.exception.status, 404)
                self.assertEqual(raised.exception.body, body)
                self.assertEqual(raised.exception.result, result)

    def test_text_errors_are_preserved(self) -> None:
        for body in ("<html><h1>404 Not Found</h1></html>", "请求被拒绝。\n", ""):
            with self.subTest(body=body):
                self.client.opener.open.side_effect = HTTPError(
                    "https://v3.leikwanhost.com/api/v1/user/services/42/forwards",
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(body.encode()),
                )
                with self.assertRaises(APIError) as raised:
                    self.client.request("GET", "services/42/forwards")
                self.assertEqual(raised.exception.status, 404)
                self.assertEqual(raised.exception.body, body)
                self.assertIsNone(raised.exception.result)

    def test_unexpected_success_body_is_preserved(self) -> None:
        for body in ('{"ok": false}', "<html>Unexpected response</html>"):
            with self.subTest(body=body):
                self.response.read.return_value = body.encode()
                with self.assertRaises(APIError) as raised:
                    self.client.request("GET", "services")
                self.assertEqual(raised.exception.status, 200)
                self.assertIs(raised.exception.headers, self.response.headers)
                self.assertEqual(raised.exception.body, body)

    def test_redirect_response_is_preserved_without_following(self) -> None:
        result = {"ok": True, "forward": {"id": 123}}
        body = json.dumps(result)
        self.client.opener.open.side_effect = HTTPError(
            "https://v3.leikwanhost.com/api/v1/user/services/42/forwards",
            302,
            "Found",
            {"Location": "/api/v1/user/forwards/123"},
            io.BytesIO(body.encode()),
        )
        with self.assertRaises(APIError) as raised:
            self.client.request("POST", "services/42/forwards", {})
        self.assertEqual(raised.exception.status, 302)
        self.assertEqual(raised.exception.body, body)
        self.assertEqual(raised.exception.result, result)
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_local_error_has_no_http_response(self) -> None:
        error = APIError("请求失败。")
        self.assertIsNone(error.status)
        self.assertIsNone(error.body)
        self.assertIsNone(error.result)
        self.assertEqual(error.headers.items(), [])
        self.assertIsNone(error.headers.get("Retry-After"))
        self.assertEqual(str(error), "请求失败。")

    def test_write_timeout_is_not_retried(self) -> None:
        self.client.opener.open.side_effect = URLError("timed out")
        with self.assertRaises(APIError):
            self.client.request(
                "POST", "services/42/forwards", {}, key="synthetic-create-01"
            )
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_incomplete_response_is_not_retried(self) -> None:
        self.response.read.side_effect = IncompleteRead(b'{"ok":', 20)
        for method in ("GET", "POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.client.opener.reset_mock()
                with self.assertRaisesRegex(APIError, "未收到完整请求结果") as error:
                    self.client.request(method, "forwards/123")
                self.assertEqual(self.client.opener.open.call_count, 1)
                self.assertEqual(
                    "写操作结果尚不确定" in str(error.exception), method != "GET"
                )

    def test_incomplete_error_response_is_handled(self) -> None:
        body = MagicMock()
        body.closed = False
        body.read.side_effect = IncompleteRead(b'{"error":', 20)
        self.client.opener.open.side_effect = HTTPError(
            "https://v3.leikwanhost.com/api/v1/user/forwards/123",
            500,
            "Internal Server Error",
            {},
            body,
        )
        with self.assertRaisesRegex(APIError, "写操作结果尚不确定"):
            self.client.request("PATCH", "forwards/123", {})
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_redirect_is_refused(self) -> None:
        request = Request("https://v3.leikwanhost.com/api/v1/user/forwards/123")
        self.assertIsNone(
            NoRedirect().redirect_request(
                request, None, 302, "", {}, "https://example.com"
            )
        )

    def test_body_limit_before_network(self) -> None:
        with self.assertRaises(ValueError):
            self.client.request("POST", "services/42/forwards", {"host": "x" * 16384})
        self.client.opener.open.assert_not_called()

    def test_token_path_follows_installation(self) -> None:
        self.assertEqual(
            leikwan_api.TOKEN_FILE,
            Path(leikwan_api.__file__).resolve().parent / "data" / "api-token",
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_windows_token_round_trip(self) -> None:
        token = b"synthetic-api-token"
        encrypted = crypt_token(token)
        self.assertNotIn(token, encrypted)
        self.assertEqual(crypt_token(encrypted, decrypt=True), token)


class TokenStorageTests(unittest.TestCase):
    def test_windows_storage_keeps_encryption(self) -> None:
        token_file = MagicMock()
        with (
            patch("sys.platform", "win32"),
            patch("leikwan_api.TOKEN_FILE", token_file),
            patch(
                "leikwan_api.crypt_token", return_value=b"encrypted-token"
            ) as encrypt,
            patch("leikwan_api.NamedTemporaryFile") as temporary,
            patch("pathlib.Path.replace") as replace,
        ):
            output = temporary.return_value.__enter__.return_value
            output.name = "synthetic-stage"
            calls = MagicMock()
            calls.attach_mock(output.close, "close")
            calls.attach_mock(replace, "replace")
            save_token("synthetic-api-token")
        encrypt.assert_called_once_with(b"synthetic-api-token")
        output.write.assert_called_once_with(b"encrypted-token")
        temporary.assert_called_once_with(
            dir=token_file.parent, prefix=".api-token-", delete_on_close=False
        )
        self.assertEqual([call[0] for call in calls.mock_calls], ["close", "replace"])
        replace.assert_called_once_with(token_file)
        token_file.write_bytes.assert_not_called()

    def test_linux_storage_is_owner_only(self) -> None:
        token_file = MagicMock()
        with (
            patch("sys.platform", "linux"),
            patch("leikwan_api.TOKEN_FILE", token_file),
            patch("leikwan_api.crypt_token") as encrypt,
            patch("leikwan_api.NamedTemporaryFile") as temporary,
            patch("pathlib.Path.replace") as replace,
        ):
            output = temporary.return_value.__enter__.return_value
            output.name = "synthetic-stage"
            save_token("synthetic-api-token")
        encrypt.assert_not_called()
        token_file.parent.chmod.assert_called_once_with(0o700)
        output.write.assert_called_once_with(b"synthetic-api-token")
        output.close.assert_called_once_with()
        replace.assert_called_once_with(token_file)

    def test_storage_failures_preserve_old_token(self) -> None:
        for stage in ("write", "close", "replace"):
            with self.subTest(stage=stage), TemporaryDirectory() as directory:
                token_file = Path(directory) / "api-token"
                token_file.write_bytes(b"synthetic-old-token")

                @contextmanager
                def staging_file(
                    *,
                    dir: Path,
                    prefix: str,
                    delete_on_close: bool,
                    failure: str = stage,
                ) -> Iterator[Any]:
                    with NamedTemporaryFile(
                        dir=dir, prefix=prefix, delete_on_close=delete_on_close
                    ) as output:
                        if failure == "write":
                            output.write(b"partial")
                            output.write = MagicMock(side_effect=OSError(failure))
                        elif failure == "close":
                            output.close = MagicMock(side_effect=OSError(failure))
                        yield output

                with (
                    patch("leikwan_api.TOKEN_FILE", token_file),
                    patch("leikwan_api.crypt_token", return_value=b"encrypted-token"),
                    patch("leikwan_api.NamedTemporaryFile", side_effect=staging_file),
                    patch("pathlib.Path.replace", side_effect=OSError("replace")),
                    self.assertRaisesRegex(OSError, stage),
                ):
                    save_token("synthetic-new-token")
                self.assertEqual(token_file.read_bytes(), b"synthetic-old-token")
                self.assertEqual(list(Path(directory).iterdir()), [token_file])

    def test_successful_storage_replaces_token(self) -> None:
        with TemporaryDirectory() as directory:
            token_file = Path(directory) / "api-token"
            token_file.write_bytes(b"synthetic-old-token")
            with (
                patch("leikwan_api.TOKEN_FILE", token_file),
                patch("leikwan_api.crypt_token", return_value=b"encrypted-token"),
            ):
                save_token("synthetic-new-token")
            expected = (
                b"encrypted-token"
                if sys.platform == "win32"
                else b"synthetic-new-token"
            )
            self.assertEqual(token_file.read_bytes(), expected)
            self.assertEqual(list(Path(directory).iterdir()), [token_file])
            if sys.platform == "linux":
                self.assertEqual(token_file.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)

    def test_linux_load_does_not_use_windows_encryption(self) -> None:
        token_file = MagicMock()
        token_file.read_bytes.return_value = b"synthetic-api-token"
        with (
            patch("sys.platform", "linux"),
            patch("leikwan_api.TOKEN_FILE", token_file),
            patch("leikwan_api.crypt_token") as encrypt,
        ):
            client = Client.from_saved_token()
        self.assertEqual(client.token, "synthetic-api-token")
        encrypt.assert_not_called()


class CLILoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.token_file = MagicMock()
        self.token_file.exists.return_value = True
        self.prompt = MagicMock(return_value="synthetic-api-token")
        self.confirmation = MagicMock(return_value="y")
        self.save = MagicMock()
        self.output = io.StringIO()

    def invoke(self, arguments: list[str]) -> None:
        with (
            patch("sys.argv", ["LeiKwanForward", "login", *arguments]),
            patch("leikwan_api.TOKEN_FILE", self.token_file),
            patch("leikwan_api.save_token", self.save),
            patch("getpass.getpass", self.prompt),
            patch("sys.stdin.isatty", return_value=not arguments),
            patch("builtins.input", self.confirmation),
            redirect_stdout(self.output),
            redirect_stderr(self.output),
        ):
            runpy.run_path(str(Path(__file__).with_name("LeiKwanForward.py")))

    def test_argument_replaces_token_without_prompt(self) -> None:
        self.invoke(["synthetic-api-token"])
        self.prompt.assert_not_called()
        self.confirmation.assert_not_called()
        self.save.assert_called_once_with("synthetic-api-token")
        self.assertEqual(self.output.getvalue(), "")

    def test_no_argument_keeps_hidden_input(self) -> None:
        self.invoke([])
        self.confirmation.assert_called_once()
        self.prompt.assert_called_once()
        self.save.assert_called_once_with("synthetic-api-token")

    def test_empty_argument_does_not_replace_token(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke([""])
        self.assertEqual(error.exception.code, 1)
        self.prompt.assert_not_called()
        self.save.assert_not_called()


class CLIInteractionTests(unittest.TestCase):
    def invoke(
        self,
        arguments: list[str],
        *,
        answer: str = "y",
        token_exists: bool = True,
        interactive: bool = True,
        request_error: BaseException | None = None,
        request_result: dict[str, Any] | None = None,
        input_error: BaseException | None = None,
    ) -> MagicMock:
        self.output = io.StringIO()
        self.diagnostics = io.StringIO()
        self.token_file = MagicMock()
        self.token_file.exists.return_value = token_exists
        client = MagicMock()
        self.client = client
        client.request.return_value = (
            request_result
            if request_result is not None
            else {"ok": True, "forward": {"id": 123}},
            '"forward-123-r3"',
        )
        client.request.side_effect = request_error
        with (
            patch("sys.argv", ["LeiKwanForward", *arguments]),
            patch("leikwan_api.TOKEN_FILE", self.token_file),
            patch(
                "leikwan_api.Client.from_saved_token", return_value=client
            ) as factory,
            patch("sys.stdin.isatty", return_value=interactive),
            patch("builtins.input", return_value=answer, side_effect=input_error),
            redirect_stdout(self.output),
            redirect_stderr(self.diagnostics),
        ):
            runpy.run_path(str(Path(__file__).with_name("LeiKwanForward.py")))
        if arguments and arguments[0] == "logout":
            factory.assert_not_called()
        return client

    def test_create_uses_numbers_and_auto_port(self) -> None:
        client = self.invoke(
            [
                "create",
                "--service",
                "42",
                "--route",
                "14",
                "--host",
                "203.0.113.10",
                "--port",
                "443",
                "--key",
                "synthetic-create-01",
            ]
        )
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(
            client.request.call_args.args,
            (
                "POST",
                "services/42/forwards",
                {
                    "route_id": 14,
                    "listen_port": None,
                    "transport": "tcp+udp",
                    "native_outbound": False,
                    "target": {"source": "manual", "host": "203.0.113.10", "port": 443},
                },
            ),
        )
        self.assertEqual(
            client.request.call_args.kwargs, {"key": "synthetic-create-01"}
        )

    def test_edit_only_supplied_field(self) -> None:
        client = self.invoke(["edit", "123", "--port", "8443"])
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(
            client.request.call_args.args,
            ("PATCH", "forwards/123", {"target": {"port": 8443}}),
        )
        self.assertEqual(client.request.call_args.kwargs, {"etag": '"forward-123-r3"'})

    def test_native_creation_has_no_target(self) -> None:
        client = self.invoke(
            ["create", "--service", "42", "--route", "14", "--native-outbound", "-y"],
            interactive=False,
        )
        self.assertEqual(
            client.request.call_args.args,
            (
                "POST",
                "services/42/forwards",
                {
                    "route_id": 14,
                    "listen_port": None,
                    "transport": "tcp+udp",
                    "native_outbound": True,
                },
            ),
        )
        client.request.assert_called_once()
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_edit_enables_native_outbound(self) -> None:
        client = self.invoke(
            ["edit", "123", "--native-outbound", "--transport", "tcp+udp", "-y"],
            interactive=False,
        )
        self.assertEqual(client.request.call_args_list[0].args, ("GET", "forwards/123"))
        self.assertEqual(
            client.request.call_args.args,
            (
                "PATCH",
                "forwards/123",
                {"native_outbound": True, "transport": "tcp+udp"},
            ),
        )
        self.assertEqual(client.request.call_args.kwargs, {"etag": '"forward-123-r3"'})
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(
            json.loads(self.output.getvalue()), {"ok": True, "forward": {"id": 123}}
        )
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_edit_disables_native_with_target(self) -> None:
        client = self.invoke(
            [
                "edit",
                "123",
                "--no-native-outbound",
                "--host",
                "example.com",
                "--port",
                "443",
                "-y",
            ],
            interactive=False,
        )
        self.assertEqual(
            client.request.call_args.args,
            (
                "PATCH",
                "forwards/123",
                {
                    "native_outbound": False,
                    "target": {"source": "manual", "host": "example.com", "port": 443},
                },
            ),
        )
        self.assertEqual(client.request.call_args.kwargs, {"etag": '"forward-123-r3"'})

    def test_invalid_native_options_do_not_send_requests(self) -> None:
        for options in (
            ["--native-outbound"],
            ["--native-outbound", "--transport", "tcp"],
            ["--native-outbound", "--transport", "udp"],
            ["--native-outbound", "--transport", "tcp+udp", "--host", "example.com"],
            ["--native-outbound", "--transport", "tcp+udp", "--target-service", "43"],
            ["--native-outbound", "--transport", "tcp+udp", "--port", "443"],
            ["--native-outbound", "--no-native-outbound", "--transport", "tcp+udp"],
            ["--no-native-outbound"],
        ):
            with self.subTest(options=options):
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(["edit", "123", *options, "-y"], interactive=False)
                self.assertEqual(raised.exception.code, 2)
                self.client.request.assert_not_called()
                self.assertEqual(self.output.getvalue(), "")

    def test_create_stdout_is_one_json_result(self) -> None:
        client = self.invoke(
            [
                "create",
                "--service",
                "42",
                "--route",
                "14",
                "--host",
                "203.0.113.10",
                "--port",
                "443",
                "--yes",
            ],
            interactive=False,
        )
        self.assertEqual(
            json.loads(self.output.getvalue()), {"ok": True, "forward": {"id": 123}}
        )
        self.assertEqual(self.diagnostics.getvalue(), "")
        self.assertEqual(client.request.call_count, 1)

    def test_edit_stdout_is_one_json_result(self) -> None:
        client = self.invoke(
            ["edit", "123", "--port", "8443", "--yes"], interactive=False
        )
        self.assertEqual(
            json.loads(self.output.getvalue()), {"ok": True, "forward": {"id": 123}}
        )
        self.assertEqual(self.diagnostics.getvalue(), "")
        self.assertEqual(client.request.call_count, 2)

    def test_delete_stdout_is_one_json_result(self) -> None:
        client = self.invoke(["delete", "123", "--yes"], interactive=False)
        self.assertEqual(
            json.loads(self.output.getvalue()), {"ok": True, "deleted_id": 123}
        )
        self.assertEqual(client.request.call_args.args, ("DELETE", "forwards/123"))
        self.assertEqual(client.request.call_args.kwargs, {"etag": '"forward-123-r3"'})
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_short_yes_matches_long_yes(self) -> None:
        for arguments in (
            [
                "create",
                "--service",
                "42",
                "--route",
                "14",
                "--host",
                "203.0.113.10",
                "--port",
                "443",
                "--key",
                "synthetic-create-01",
            ],
            ["edit", "123", "--port", "8443"],
            ["edit", "123", "--native-outbound", "--transport", "tcp+udp"],
            ["delete", "123"],
            ["logout"],
        ):
            with self.subTest(command=arguments[0]):
                short = self.invoke([*arguments, "-y"], interactive=False)
                short_calls = short.request.call_args_list
                short_output = self.output.getvalue()
                self.assertEqual(self.diagnostics.getvalue(), "")
                if arguments[0] == "logout":
                    self.token_file.unlink.assert_called_once_with()
                long = self.invoke([*arguments, "--yes"], interactive=False)
                self.assertEqual(long.request.call_args_list, short_calls)
                self.assertEqual(self.output.getvalue(), short_output)
                self.assertEqual(self.diagnostics.getvalue(), "")
                if arguments[0] == "logout":
                    self.token_file.unlink.assert_called_once_with()

    def test_failed_creation_preserves_request_key(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke(
                [
                    "create",
                    "--service",
                    "42",
                    "--route",
                    "14",
                    "--host",
                    "203.0.113.10",
                    "--port",
                    "443",
                    "--key",
                    "synthetic-create-01",
                    "--yes",
                ],
                interactive=False,
                request_error=APIError("未收到完整请求结果。"),
            )
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(self.output.getvalue(), "")
        self.assertIn(
            "Idempotency-Key: synthetic-create-01", self.diagnostics.getvalue()
        )

    def test_failed_creation_preserves_platform_body(self) -> None:
        body = '{"ok": false, "error": {"code": "validation_failed", "fields": {"target.host": "reserved_range"}}}'
        failure = APIError("HTTP 422", status=422, body=body, result=json.loads(body))
        with self.assertRaises(SystemExit) as raised:
            self.invoke(
                [
                    "create",
                    "--service",
                    "42",
                    "--route",
                    "14",
                    "--host",
                    "localhost",
                    "--port",
                    "443",
                    "--key",
                    "synthetic-create-01",
                    "-y",
                ],
                interactive=False,
                request_error=failure,
            )
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(self.output.getvalue(), body + "\n")
        self.assertEqual(
            self.diagnostics.getvalue(),
            "Idempotency-Key: synthetic-create-01\nLeiKwanForward: HTTP 422\n",
        )
        self.client.request.assert_called_once()

    def test_interrupted_creation_preserves_request_key(self) -> None:
        for supplied_key in (None, "synthetic-create-01"):
            with self.subTest(key=supplied_key):
                arguments = [
                    "create",
                    "--service",
                    "42",
                    "--route",
                    "14",
                    "--host",
                    "203.0.113.10",
                    "--port",
                    "443",
                    "--yes",
                ]
                if supplied_key is not None:
                    arguments.extend(["--key", supplied_key])
                with self.assertRaises(SystemExit) as error:
                    self.invoke(
                        arguments, interactive=False, request_error=KeyboardInterrupt()
                    )
                self.assertEqual(error.exception.code, 130)
                self.client.request.assert_called_once()
                sent_key = self.client.request.call_args.kwargs["key"]
                self.assertIn(
                    f"Idempotency-Key: {sent_key}", self.diagnostics.getvalue()
                )
                self.assertIn("创建结果尚不确定", self.diagnostics.getvalue())
                self.assertNotIn("已取消", self.diagnostics.getvalue())
                self.assertEqual(self.output.getvalue(), "")
                if supplied_key is not None:
                    self.assertEqual(sent_key, supplied_key)

    def test_creation_interrupted_before_confirmation_does_not_send(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke(
                [
                    "create",
                    "--service",
                    "42",
                    "--route",
                    "14",
                    "--host",
                    "203.0.113.10",
                    "--port",
                    "443",
                ],
                input_error=KeyboardInterrupt(),
            )
        self.assertEqual(error.exception.code, 130)
        self.client.request.assert_not_called()
        self.assertNotIn("Idempotency-Key", self.diagnostics.getvalue())

    def test_prompt_is_not_written_to_stdout(self) -> None:
        self.invoke(["edit", "123", "--port", "8443"])
        self.assertEqual(
            json.loads(self.output.getvalue()), {"ok": True, "forward": {"id": 123}}
        )
        self.assertIn("[y/N]", self.diagnostics.getvalue())
        self.assertIn('"port": 8443', self.diagnostics.getvalue())

    def test_delete_cancellation_does_not_write(self) -> None:
        client = self.invoke(["delete", "123"], answer="n")
        client.request.assert_called_once_with("GET", "forwards/123")
        self.assertEqual(self.output.getvalue(), "")

    def test_noninteractive_write_requires_yes(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke(["edit", "123", "--port", "8443"], interactive=False)
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(self.output.getvalue(), "")
        self.assertIn("--yes", self.diagnostics.getvalue())

    def test_status_checks_service_access(self) -> None:
        client = self.invoke(["status", "--service", "42"])
        client.request.assert_called_once_with("GET", "services/42/forward-options")
        self.assertEqual(
            json.loads(self.output.getvalue()),
            {"ok": True, "authenticated": True, "service_id": 42},
        )

    def test_services_preserves_complete_platform_result(self) -> None:
        result = {
            "ok": True,
            "services": [
                {
                    "id": 42,
                    "status": "active",
                    "forward_limit": 64,
                    "active_forward_count": 2,
                }
            ],
        }
        client = self.invoke(["services"], request_result=result, interactive=False)
        client.request.assert_called_once_with("GET", "services")
        self.assertEqual(json.loads(self.output.getvalue()), result)
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_http_errors_separate_body_and_diagnostics(self) -> None:
        for body in (
            '{"ok": false, "error": {"code": "service_not_found", "message": "Not found."}}',
            '{"error": "not_found", "path": "/api/user/services"}',
            "<html><h1>404 Not Found</h1></html>",
            "请求被拒绝。\n",
            "",
        ):
            with self.subTest(body=body):
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(
                        ["services"],
                        request_error=APIError("HTTP 404", status=404, body=body),
                    )
                self.assertEqual(raised.exception.code, 1)
                expected = body + ("\n" if body and not body.endswith("\n") else "")
                self.assertEqual(self.output.getvalue(), expected)
                self.assertEqual(
                    self.diagnostics.getvalue(), "LeiKwanForward: HTTP 404\n"
                )

    def test_local_errors_do_not_write_response_body(self) -> None:
        for failure in (APIError("未收到完整请求结果。"), ValueError("API令牌无效。")):
            with self.subTest(error=type(failure).__name__):
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(["services"], request_error=failure)
                self.assertEqual(raised.exception.code, 1)
                self.assertEqual(self.output.getvalue(), "")
                self.assertEqual(
                    self.diagnostics.getvalue(), f"LeiKwanForward: {failure}\n"
                )

    def test_queries_write_one_json_result(self) -> None:
        for arguments, path in (
            (["services"], "services"),
            (["routes", "--service", "42"], "services/42/forward-options"),
            (["list", "--service", "42"], "services/42/forwards"),
            (["show", "123"], "forwards/123"),
        ):
            with self.subTest(command=arguments[0]):
                client = self.invoke(arguments)
                client.request.assert_called_once_with("GET", path)
                self.assertEqual(
                    json.loads(self.output.getvalue()),
                    {"ok": True, "forward": {"id": 123}},
                )

    def test_logout_only_removes_local_token(self) -> None:
        client = self.invoke(["logout", "--yes"], interactive=False)
        self.token_file.unlink.assert_called_once_with()
        client.request.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_logout_cancellation_keeps_token(self) -> None:
        self.invoke(["logout"], answer="n")
        self.token_file.unlink.assert_not_called()

    def test_logout_without_token_succeeds(self) -> None:
        self.invoke(["logout", "--yes"], token_exists=False, interactive=False)
        self.token_file.unlink.assert_not_called()
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_logout_noninteractive_requires_yes(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke(["logout"], interactive=False)
        self.assertEqual(error.exception.code, 1)
        self.token_file.unlink.assert_not_called()

    def test_default_help_lists_commands(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke([])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("commands:", self.output.getvalue())
        self.assertIn("create", self.output.getvalue())
        self.assertNotIn("示例", self.output.getvalue())
        self.assertNotIn("Headless", self.output.getvalue())
        self.assertEqual(self.diagnostics.getvalue(), "")

    def test_explicit_help_includes_examples(self) -> None:
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                with self.assertRaises(SystemExit) as error:
                    self.invoke([flag])
                self.assertEqual(error.exception.code, 0)
                self.assertIn("commands:", self.output.getvalue())
                self.assertIn("示例", self.output.getvalue())
                self.assertIn(
                    'LeiKwanForward login "YOUR_API_TOKEN"', self.output.getvalue()
                )
                self.assertEqual(self.diagnostics.getvalue(), "")

    def test_subcommand_help_lists_usage_and_options(self) -> None:
        for command in (
            "login",
            "logout",
            "services",
            "status",
            "routes",
            "list",
            "show",
            "create",
            "edit",
            "delete",
        ):
            with self.subTest(command=command):
                with self.assertRaises(SystemExit) as error:
                    self.invoke([command, "--help"])
                self.assertEqual(error.exception.code, 0)
                self.assertIn(
                    f"usage: LeiKwanForward {command}", self.output.getvalue()
                )
                self.assertIn("--help", self.output.getvalue())
                self.assertIn("示例", self.output.getvalue())
                if command in {"logout", "create", "edit", "delete"}:
                    self.assertIn("-y, --yes", self.output.getvalue())
                if command == "edit":
                    self.assertIn("--native-outbound", self.output.getvalue())
                    self.assertIn("--no-native-outbound", self.output.getvalue())
                self.assertEqual(self.diagnostics.getvalue(), "")

    def test_short_subcommand_help(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.invoke(["login", "-h"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("usage: LeiKwanForward login", self.output.getvalue())
        self.assertIn("TOKEN", self.output.getvalue())
        self.assertIn("示例", self.output.getvalue())
        self.assertEqual(self.diagnostics.getvalue(), "")
