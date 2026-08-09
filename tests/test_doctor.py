"""Tests for learntok.doctor environment checks."""
import os
import tempfile
import unittest
from unittest import mock

from learntok import doctor


class DoctorCheckTest(unittest.TestCase):
    def test_check_python_ok(self):
        result = doctor.check_python()
        self.assertEqual(result["status"], "OK")

    def test_check_package_ok(self):
        result = doctor.check_package()
        self.assertEqual(result["status"], "OK")

    def test_check_characters_ok_in_repo(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = doctor.check_characters()
        self.assertEqual(result["status"], "OK")

    def test_check_manifest_ok_in_repo(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = doctor.check_manifest()
        self.assertEqual(result["status"], "OK")

    def test_check_ffmpeg_ok_in_repo(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = doctor.check_ffmpeg()
        self.assertEqual(result["status"], "OK")

    def test_workspace_writable_ok(self):
        with tempfile.TemporaryDirectory() as d:
            result = doctor.check_workspace_writable(d)
        self.assertEqual(result["status"], "OK")

    def test_workspace_writable_fail(self):
        with tempfile.TemporaryDirectory() as d:
            # point output/ at an existing *file* so makedirs fails
            os.makedirs(os.path.join(d, "output"))
            with open(os.path.join(d, "output", "blocker"), "w", encoding="utf-8") as fh:
                fh.write("x")
            result = doctor.check_workspace_writable(d, probe_name="blocker/sub")
            self.assertEqual(result["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()