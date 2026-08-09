"""Tests for learntok.cli subcommand mapping and init."""
import os
import sys
import tempfile
import unittest

from learntok import cli


class CliMappingTest(unittest.TestCase):
    def test_module_for_known_subcommands(self):
        cases = {
            "make": "learntok.tools.run_pipeline",
            "script-gen": "learntok.tools.script_gen",
            "tts": "learntok.tools.tts_edge",
            "rvc": "learntok.tools.rvc_convert",
            "calibrate": "learntok.tools.calibrate_audio",
            "compose": "learntok.compose",
            "validate": "learntok.tools.validate_script",
            "fix": "learntok.tools.script_fix",
            "ingest-srt": "learntok.tools.ingest_srt",
            "migrate-terms": "learntok.tools.migrate_terms",
            "rag-build": "learntok.tools.rag_build",
            "rag-retrieve": "learntok.tools.rag_retrieve",
            "doctor": "learntok.doctor",
        }
        for sub, mod in cases.items():
            self.assertEqual(cli.module_for(sub), mod)

    def test_unknown_subcommand_raises(self):
        with self.assertRaises(KeyError):
            cli.module_for("nope")

    def test_build_command_passthrough(self):
        argv = cli.build_command("validate", ["--script", "x.json"])
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], "-m")
        self.assertEqual(argv[2], "learntok.tools.validate_script")
        self.assertEqual(argv[3:], ["--script", "x.json"])


class InitTest(unittest.TestCase):
    def test_init_creates_workspace_skeleton(self):
        with tempfile.TemporaryDirectory() as d:
            cli.init_workspace(d)
            for rel in ("output", "pipeline/build"):
                self.assertTrue(os.path.isdir(os.path.join(d, rel)))
            self.assertTrue(os.path.isfile(os.path.join(d, "output", ".gitkeep")))
            self.assertTrue(os.path.isfile(os.path.join(d, "pipeline", "build", ".gitkeep")))


if __name__ == "__main__":
    unittest.main()