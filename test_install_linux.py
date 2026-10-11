"""Linux installation preview must not modify files."""

import io
import runpy
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "linux", "Linux installer")
class LinuxInstallerTests(unittest.TestCase):
    def test_preview_does_not_write(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch("sys.argv", ["install-linux-forward.py", "--dry-run"]),
            patch("pathlib.Path.exists", return_value=False),
            patch("pathlib.Path.mkdir") as mkdir,
            patch("shutil.copyfile") as copyfile,
            patch("pathlib.Path.write_text") as write_text,
            redirect_stdout(output),
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as result,
        ):
            runpy.run_path(str(Path(__file__).with_name("install-linux-forward.py")))
        self.assertEqual(result.exception.code, 0)
        self.assertIn("/opt/LeiKwanForward", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        mkdir.assert_not_called()
        copyfile.assert_not_called()
        write_text.assert_not_called()
