"""Tests for learntok.config workspace resolution."""
import os
import tempfile
import unittest
from unittest import mock

import learntok.config as config


def _make_workspace(tmp):
    assets = os.path.join(tmp, "assets")
    os.makedirs(assets)
    with open(os.path.join(assets, "characters.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    return tmp


class WorkspaceRootTest(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(
            os.environ, {config.ENV_WORKSPACE: d}
        ):
            self.assertEqual(config.workspace_root(), os.path.abspath(d))

    def test_marker_walk_up_finds_repo(self):
        with tempfile.TemporaryDirectory() as d:
            _make_workspace(d)
            sub = os.path.join(d, "materials", "genai")
            os.makedirs(sub)
            self.assertEqual(config.workspace_root(start=sub), os.path.abspath(d))

    def test_no_marker_falls_back_to_start(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(config.workspace_root(start=d), os.path.abspath(d))

    def test_config_file_root(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "learntok.toml"), "w", encoding="utf-8") as fh:
                fh.write('[workspace]\nroot = "C:/some/workspace"\n')
            self.assertEqual(config.workspace_root(start=d), os.path.abspath("C:/some/workspace"))


    def test_config_file_relative_root(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "project")
            os.makedirs(sub)
            with open(os.path.join(sub, "learntok.toml"), "w", encoding="utf-8") as fh:
                fh.write('[workspace]\nroot = "../ws"\n')
            self.assertEqual(config.workspace_root(start=sub),
                             os.path.abspath(os.path.join(sub, "..", "ws")))


class AssetsRootTest(unittest.TestCase):
    def test_env_override(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(
            os.environ, {config.ENV_ASSETS: d}
        ):
            self.assertEqual(config.assets_root(), os.path.abspath(d))

    def test_default_under_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            _make_workspace(d)
            with mock.patch.dict(
                os.environ, {config.ENV_WORKSPACE: d}, clear=True
            ):
                self.assertEqual(
                    config.assets_root(), os.path.join(d, "assets")
                )


class FfmpegTest(unittest.TestCase):
    def test_bundled_dir_detected(self):
        with tempfile.TemporaryDirectory() as d:
            _make_workspace(d)
            os.makedirs(os.path.join(d, "pipeline", "tools", "ffmpeg"))
            with mock.patch.dict(
                os.environ, {config.ENV_WORKSPACE: d}, clear=True
            ):
                self.assertEqual(
                    config.ffmpeg_dir(),
                    os.path.join(d, "pipeline", "tools", "ffmpeg"),
                )

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(
            os.environ, {config.ENV_FFMPEG_DIR: d}
        ):
            self.assertEqual(config.ffmpeg_dir(), os.path.abspath(d))

    def test_find_tool_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(
            os.environ, {config.ENV_FFMPEG_DIR: d}
        ), mock.patch("learntok.config.shutil.which", return_value=None):
            self.assertIsNone(config.find_tool("ffmpeg", exit_on_missing=False))

    def test_find_tool_missing_exits(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(
            os.environ, {config.ENV_FFMPEG_DIR: d}
        ), mock.patch("learntok.config.shutil.which", return_value=None):
            with self.assertRaises(SystemExit):
                config.find_tool("ffmpeg")




class LoadEnvTest(unittest.TestCase):
    def test_loads_tools_env_then_root_env(self):
        try:
            import dotenv  # noqa: F401
        except ImportError:
            self.skipTest("python-dotenv not installed")
        with tempfile.TemporaryDirectory() as d:
            ws = os.path.join(d, "ws")
            os.makedirs(os.path.join(ws, "pipeline", "tools"))
            with open(os.path.join(ws, ".env"), "w", encoding="utf-8") as fh:
                fh.write("DEEPSEEK_API_KEY=from-root\n")
            with open(os.path.join(ws, "pipeline", "tools", ".env"), "w", encoding="utf-8") as fh:
                fh.write("DEEPSEEK_API_KEY=from-tools\n")
            with mock.patch.dict(os.environ, {config.ENV_WORKSPACE: ws}, clear=True):
                config.load_env()
                self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "from-tools")


if __name__ == "__main__":
    unittest.main()