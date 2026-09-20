# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from learntok.tools import series_bible as sb
from learntok.tools import validate_script as vs
from learntok.tools.script_fix import apply_fixes, fix_questioner_voice
from learntok import cli


def _script(lines):
    return {
        "id": "t",
        "title": "t",
        "characters": {
            "A": {"name": "企鵝燈", "role": "questioner", "color": "#FFD54F"},
            "B": {"name": "熊大", "role": "explainer", "color": "#81C784"},
        },
        "lines": lines,
    }


class ValidateVoiceTest(unittest.TestCase):
    def test_b_confused_opener_is_error_when_strict(self):
        script = _script([
            {"speaker": "B", "text": "等等，選擇權是什麼。聽起來好複雜。"},
            {"speaker": "A", "text": "那不就是賭博嗎？"},
            {"speaker": "B", "text": "俺打個比方你先付訂金。"},
            {"speaker": "A", "text": "歐式美式差在哪裡？"},
            {"speaker": "B", "text": "歐式只能到期才執行。"},
            {"speaker": "B", "text": "美式隨時都能執行權利。"},
            {"speaker": "A", "text": "原來權利可以放棄啊咕咕嘎嘎！"},
        ])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(script, fh)
            errors, warnings = vs.validate(path, strict_voice=True)
        joined = "\n".join(errors)
        self.assertTrue(any("questioner voice" in e or "opener" in e for e in errors), joined)

    def test_gugu_on_b_is_error(self):
        script = _script([
            {"speaker": "A", "text": "選擇權到底在賣什麼？"},
            {"speaker": "B", "text": "俺先講權利不是義務。"},
            {"speaker": "A", "text": "那我可以不買對嗎？"},
            {"speaker": "B", "text": "對，價錢不好就能放棄。"},
            {"speaker": "A", "text": "聽起來像訂金耶。"},
            {"speaker": "B", "text": "機器學習真難懂，咕咕嘎嘎！"},
        ])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(script, fh)
            errors, _warnings = vs.validate(path)
        self.assertTrue(any("gugu closer must be A" in e for e in errors), errors)

    def test_min_lines(self):
        script = _script([
            {"speaker": "A", "text": "這是一個問句嗎？"},
            {"speaker": "B", "text": "這是一句講解內容。"},
            {"speaker": "A", "text": "那我再追問一次？"},
            {"speaker": "B", "text": "俺再用例子說明一次。"},
            {"speaker": "A", "text": "懂了謝謝你咕咕嘎嘎！"},
        ])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(script, fh)
            errors, _ = vs.validate(path, min_lines=80)
        self.assertTrue(any("below min" in e or "line count" in e for e in errors), errors)


class BibleSchemaTest(unittest.TestCase):
    def test_sample_bible_validates(self):
        path = os.path.join("pipeline", "examples", "sample_bible.json")
        bible = sb.load_json(path)
        errs = sb.validate_bible(bible)
        self.assertEqual(errs, [], errs)
        self.assertIn(bible["status"], ("draft", "approved"))
        self.assertGreaterEqual(len(bible["episodes"]), 4)
        self.assertEqual(bible["episodes"][0].get("recap_from_prev") or "", "")

    def test_missing_recap_on_later_episode(self):
        bible = {
            "series": "x", "title": "t", "audience": "a",
            "throughline": "th", "learning_path": ["q1"],
            "episodes": [
                {"id": "ep01", "title": "t1", "one_liner": "o", "must_cover": ["a"],
                 "do_not_cover": ["b"], "hook": "h"},
                {"id": "ep02", "title": "t2", "one_liner": "o", "must_cover": ["a"],
                 "do_not_cover": ["b"], "hook": "h"},
            ],
        }
        errs = sb.validate_bible(bible)
        self.assertTrue(any("recap_from_prev" in e for e in errs), errs)


class ScriptFixVoiceTest(unittest.TestCase):
    def test_fix_moves_confused_b_opener(self):
        script = _script([
            {"speaker": "B", "text": "等等，選擇權是什麼。聽起來好複雜。"},
            {"speaker": "A", "text": "那不就是賭博嗎？"},
            {"speaker": "B", "text": "俺打個比方你先付訂金。"},
            {"speaker": "B", "text": "價錢不好你可以放棄。"},
            {"speaker": "A", "text": "歐式美式差在哪裡？"},
            {"speaker": "B", "text": "歐式只能到期才執行。"},
            {"speaker": "A", "text": "原來如此咕咕嘎嘎！"},
        ])
        lines, changed = fix_questioner_voice(script["lines"])
        self.assertTrue(changed)
        self.assertEqual(lines[0]["speaker"], "A")


class CliMappingTest(unittest.TestCase):
    def test_series_subcommands_map_to_series_bible(self):
        for sub in ("series-bible", "series-outline", "series-gen", "script-review"):
            self.assertEqual(cli.module_for(sub), "learntok.tools.series_bible")


class ReviewGateTest(unittest.TestCase):
    def test_llm_review_requires_explicit_true(self):
        self.assertTrue(sb.llm_review_passed({"pass": True}))
        self.assertFalse(sb.llm_review_passed({}))
        self.assertFalse(sb.llm_review_passed({"pass": None}))
        self.assertFalse(sb.llm_review_passed({"pass": "false"}))
        self.assertFalse(sb.llm_review_passed({"pass": False}))


class OutlineSectionsTest(unittest.TestCase):
    def test_reviewed_outline_keeps_all_sections(self):
        ep = {"target_sections": 8}
        outline = {"sections": [{"title": str(i)} for i in range(10)]}
        self.assertEqual(sb.max_sections_for_gen(0, ep, outline), 10)

    def test_explicit_max_sections_wins(self):
        ep = {"target_sections": 8}
        outline = {"sections": [{"title": str(i)} for i in range(10)]}
        self.assertEqual(sb.max_sections_for_gen(8, ep, outline), 8)

    def test_fallback_to_target_without_outline(self):
        self.assertEqual(sb.max_sections_for_gen(0, {"target_sections": 8}, None), 8)


class ResolveBibleTest(unittest.TestCase):
    def test_picks_episode_by_script_id(self):
        bible = sb.load_json(os.path.join("pipeline", "examples", "sample_bible.json"))
        resolved = vs.resolve_bible(bible, script_id="ep02-mechanism")
        self.assertEqual(resolved["episode"]["id"], "ep02-mechanism")
        self.assertTrue(resolved["episode"]["recap_from_prev"])

    def test_missing_episode_returns_none(self):
        bible = sb.load_json(os.path.join("pipeline", "examples", "sample_bible.json"))
        self.assertIsNone(vs.resolve_bible(bible, script_id="nope"))

    def test_already_resolved_episode_passthrough(self):
        resolved = vs.resolve_bible({"episode": {"id": "ep01", "recap_from_prev": ""}})
        self.assertEqual(resolved["episode"]["id"], "ep01")


if __name__ == "__main__":
    unittest.main()
