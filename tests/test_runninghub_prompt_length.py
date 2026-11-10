import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class RunningHubPromptLengthTests(unittest.TestCase):
    def _params(self, **extra):
        field = {"fieldKey": "prompt", "type": "STRING", "required": True}
        field.update(extra)
        return [field, {"fieldKey": "aspectRatio", "type": "LIST", "options": [{"value": "1:1"}]}]

    def _field(self, **extra):
        return main.runninghub_prompt_field(self._params(**extra))

    def test_prompt_field_is_resolved_from_schema(self):
        field = self._field(maxLength=2000)

        self.assertIsNotNone(field)
        self.assertEqual(main.runninghub_field_max_length(field), 2000)

    def test_prompt_field_accepts_common_aliases(self):
        for key in ("text", "positive", "promptText"):
            with self.subTest(key=key):
                params = [{"fieldKey": key, "type": "TEXT", "maxLength": 512}]
                field = main.runninghub_prompt_field(params)

                self.assertEqual(main.runninghub_field_max_length(field), 512)

    def test_missing_or_invalid_max_length_is_treated_as_unlimited(self):
        self.assertEqual(main.runninghub_field_max_length(None), 0)
        self.assertEqual(main.runninghub_field_max_length({"maxLength": "abc"}), 0)
        self.assertEqual(main.runninghub_field_max_length({"maxLength": -5}), 0)
        self.assertEqual(main.runninghub_field_max_length({"maxLength": 0}), 0)

    def test_prompt_at_limit_passes(self):
        detail = main.runninghub_ensure_prompt_length(self._field(maxLength=2000), "a" * 2000, "seedream-v5-pro/text-to-image")

        self.assertEqual(detail, 2000)

    def test_prompt_over_limit_raises_actionable_400(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.runninghub_ensure_prompt_length(self._field(maxLength=2000), "a" * 6073, "seedream-v5-pro/text-to-image")

        detail = str(ctx.exception.detail)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("提示词上限是 2000 字", detail)
        self.assertIn("6073", detail)
        self.assertIn("超出 4073 字", detail)
        self.assertIn("拆成多个提示词节点", detail)

    def test_field_without_declared_limit_never_blocks(self):
        self.assertEqual(main.runninghub_ensure_prompt_length({"fieldKey": "prompt"}, "a" * 9000), 0)

    def test_upstream_1007_is_translated_for_prompt_field(self):
        raw = {
            "taskId": "",
            "status": "",
            "errorCode": "1007",
            "errorMessage": "field 'prompt' length 6073 is greater than the maximum length 2000",
            "results": None,
        }

        detail = main.runninghub_prompt_payload_error(raw)

        self.assertIn("提示词上限是 2000 字", detail)
        self.assertIn("6073", detail)
        self.assertNotIn("errorCode", detail)

    def test_upstream_1007_is_translated_for_other_fields(self):
        raw = {"code": 1007, "msg": "field 'aspectRatio' length 4 is greater than the maximum length 3"}

        detail = main.runninghub_prompt_payload_error(raw)

        self.assertIn("aspectRatio", detail)
        self.assertIn("上限 3", detail)

    def test_unrelated_payload_errors_are_left_alone(self):
        self.assertEqual(main.runninghub_prompt_payload_error({"errorMessage": "insufficient balance"}), "")
        self.assertEqual(main.runninghub_prompt_payload_error({"errorMessage": "field 'prompt' is required"}), "")
        self.assertEqual(main.runninghub_prompt_payload_error("not a dict"), "")

    def test_friendly_detail_replaces_raw_1007_json(self):
        raw = ("{'taskId': '', 'status': '', 'errorCode': '1007', "
               "\"errorMessage\": \"field 'prompt' length 6073 is greater than the maximum length 2000\"}")

        detail = main.friendly_image_error_detail(raw, model="seedream-v5-pro/text-to-image")

        self.assertIn("提示词上限是 2000 字", detail)
        self.assertIn("6073", detail)
        self.assertNotIn("errorCode", detail)

    def test_non_runninghub_provider_skips_capability_lookup(self):
        result = asyncio.run(
            main.ensure_runninghub_prompt_capability(
                {"id": "openai", "name": "OpenAI", "protocol": "openai"},
                "gpt-image-2",
                "a" * 6000,
            )
        )

        self.assertEqual(result, 0)


class RunningHubLearnedPromptLimitTests(unittest.TestCase):
    """注册表声明值与上游实际校验不一致时，以上游拒绝过的上限为准。"""

    MODEL = "seedream-v5-pro/text-to-image"

    def setUp(self):
        main.RUNNINGHUB_PROMPT_LIMIT_CACHE.clear()

    def tearDown(self):
        main.RUNNINGHUB_PROMPT_LIMIT_CACHE.clear()

    def _provider(self, profile_id="legacy-shared"):
        return {"id": "runninghub", "_api_profile_id": profile_id}

    def _params(self, max_length=5000):
        return [
            {"fieldKey": "prompt", "type": "STRING", "required": True, "maxLength": max_length},
            {"fieldKey": "aspectRatio", "type": "LIST", "options": [{"value": "1:1"}]},
        ]

    def test_prompt_field_prefers_prompt_over_other_long_text_fields(self):
        params = [
            {"fieldKey": "positivePrompt", "type": "STRING", "maxLength": 5000},
            {"fieldKey": "prompt", "type": "STRING", "maxLength": 2000},
            {"fieldKey": "text", "type": "STRING", "maxLength": 9000},
        ]

        field = main.runninghub_prompt_field(params)

        self.assertEqual(main.runninghub_field_max_length(field), 2000)

    def test_upstream_rejection_records_the_real_limit(self):
        provider = self._provider()
        raw = {"errorMessage": "field 'prompt' length 3085 is greater than the maximum length 2000"}

        detail = main.runninghub_prompt_payload_error(raw, provider, self.MODEL)

        self.assertIn("提示词上限是 2000 字", detail)
        self.assertIn(self.MODEL, detail)
        self.assertEqual(main.runninghub_learned_prompt_limit(provider, self.MODEL), 2000)

    def test_learned_limit_is_scoped_per_model_and_profile(self):
        provider = self._provider()
        main.runninghub_learn_prompt_limit(provider, self.MODEL, 2000)

        self.assertEqual(main.runninghub_learned_prompt_limit(provider, "seedream-v4/text-to-image"), 0)
        self.assertEqual(main.runninghub_learned_prompt_limit(self._provider("other-group"), self.MODEL), 0)

    def test_learned_limit_overrides_looser_schema_declaration(self):
        provider = self._provider()
        main.runninghub_learn_prompt_limit(provider, self.MODEL, 2000)
        field = main.runninghub_prompt_field(self._params(5000))

        with self.assertRaises(main.HTTPException) as ctx:
            main.runninghub_ensure_prompt_length(field, "a" * 3085, self.MODEL, provider)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("提示词上限是 2000 字", str(ctx.exception.detail))

    def test_capability_reports_learned_limit(self):
        provider = self._provider()
        main.runninghub_learn_prompt_limit(provider, self.MODEL, 2000)
        model_def = {"name_en": self.MODEL, "output_type": "image", "params": self._params(5000)}

        capability = main.public_runninghub_model_capabilities(model_def, provider, self.MODEL)
        prompt_field = next(field for field in capability["fields"] if field["key"] == "prompt")

        self.assertEqual(prompt_field["maxLength"], 2000)

    def test_capability_keeps_stricter_schema_value(self):
        provider = self._provider()
        main.runninghub_learn_prompt_limit(provider, self.MODEL, 5000)
        model_def = {"name_en": self.MODEL, "output_type": "image", "params": self._params(800)}

        capability = main.public_runninghub_model_capabilities(model_def, provider, self.MODEL)
        prompt_field = next(field for field in capability["fields"] if field["key"] == "prompt")

        self.assertEqual(prompt_field["maxLength"], 800)


if __name__ == "__main__":
    unittest.main()