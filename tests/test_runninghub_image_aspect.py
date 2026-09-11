import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class RunningHubImageAspectTests(unittest.TestCase):
    def test_image_request_dimensions_follow_standard_dimension_mode(self):
        self.assertEqual(main.snap_size_to_multiple("1234x2345", 16), "1248x2352")
        payload = main.OnlineImageRequest(
            prompt="image",
            size="1234x2345",
        )
        self.assertEqual(main.online_image_request_size(payload), "1248x2352")
        custom_dimension_payload = main.OnlineImageRequest(
            prompt="image",
            size="2047x1711",
            resolution="__custom_dimensions__",
        )
        self.assertEqual(main.online_image_request_size(custom_dimension_payload), "2048x1712")

    def test_empty_optional_aspect_is_omitted_for_source_adaptation(self):
        params = [{
            "fieldKey": "aspectRatio",
            "required": False,
            "defaultValue": "empty",
            "options": [{"value": "1:1"}, {"value": "2:3"}],
        }]
        body = {"prompt": "edit"}

        skipped = main.runninghub_apply_image_aspect(body, params, "empty", "1024x1024")
        main.runninghub_apply_schema_defaults(body, params, skip_keys=skipped)

        self.assertNotIn("aspectRatio", body)

    def test_explicit_aspect_is_preserved(self):
        params = [{
            "fieldKey": "aspectRatio",
            "required": False,
            "options": [{"value": "1:1"}, {"value": "2:3"}],
        }]
        body = {"prompt": "edit"}

        skipped = main.runninghub_apply_image_aspect(body, params, "2:3", "1024x1024")
        main.runninghub_apply_schema_defaults(body, params, skip_keys=skipped)

        self.assertEqual(body["aspectRatio"], "2:3")

    def test_internal_control_keys_are_not_serialized_as_model_params(self):
        params = [{"fieldKey": "seed", "type": "INT"}]

        result = main.runninghub_image_model_params(
            params,
            {
                "__internal_control": True,
                "seed": "42",
            },
        )

        self.assertEqual(result, {"seed": 42})

    def test_required_aspect_uses_size_fallback(self):
        params = [{
            "fieldKey": "aspectRatio",
            "required": True,
            "options": [{"value": "1:1"}, {"value": "3:2"}],
        }]
        body = {"prompt": "generate"}

        skipped = main.runninghub_apply_image_aspect(body, params, "", "1536x1024")
        main.runninghub_apply_schema_defaults(body, params, skip_keys=skipped)

        self.assertEqual(body["aspectRatio"], "3:2")

    def _gpt_image_25_model(self, *, image_input, ratios_extra=None, required=False):
        params = [{"fieldKey": "prompt", "type": "STRING", "required": True}]
        if image_input:
            params.append({"fieldKey": "imageUrls", "type": "IMAGE", "required": True})
        params.append({
            "fieldKey": "aspectRatio",
            "type": "LIST",
            "required": required,
            "defaultValue": "16:9",
            "options": [{"value": value} for value in (ratios_extra or [
                "1:1", "3:2", "2:3", "5:4", "4:5", "16:9", "9:16",
                "21:9", "3:4", "4:3", "9:21", "1:2", "2:1", "1:3", "3:1",
            ])],
        })
        params.append({
            "fieldKey": "resolution",
            "type": "LIST",
            "defaultValue": "1k",
            "options": [{"value": "1k"}, {"value": "2k"}],
        })
        return {"name_en": "gpt-image-2.5/sunburst/image-to-image/economy", "output_type": "image", "params": params}

    def _ratio_field(self, capability):
        field = next(item for item in capability["fields"] if item["key"] == "aspectRatio")
        return field

    def test_image_to_image_capability_gains_adaptive_ratio_option(self):
        # 上游只声明具体比例时，图生图能力补一个 Auto，选中即“不指定比例”，
        # 由上游沿用输入图比例。
        capability = main.public_runninghub_model_capabilities(self._gpt_image_25_model(image_input=True))
        field = self._ratio_field(capability)

        self.assertEqual(field["options"][0], {"value": "auto", "label": "自适应"})
        self.assertEqual(len(field["options"]), 16)
        self.assertEqual(field["default"], "16:9")

    def test_text_to_image_capability_keeps_declared_ratios(self):
        # 文生图没有输入图可沿用，省略 aspectRatio 只会落回上游默认档，
        # 不能补出名不副实的 Auto。
        capability = main.public_runninghub_model_capabilities(self._gpt_image_25_model(image_input=False))

        self.assertEqual(len(self._ratio_field(capability)["options"]), 15)

    def test_declared_adaptive_ratio_is_not_duplicated(self):
        for declared in ("empty", "auto", "adaptive"):
            with self.subTest(declared=declared):
                capability = main.public_runninghub_model_capabilities(
                    self._gpt_image_25_model(image_input=True, ratios_extra=[declared, "1:1", "16:9"])
                )
                values = [option["value"] for option in self._ratio_field(capability)["options"]]

                self.assertEqual(values, [declared, "1:1", "16:9"])

    def test_required_ratio_field_is_never_adaptive(self):
        capability = main.public_runninghub_model_capabilities(
            self._gpt_image_25_model(image_input=True, required=True)
        )

        self.assertNotIn("auto", [option["value"] for option in self._ratio_field(capability)["options"]])

    def test_video_model_capability_is_untouched(self):
        model = self._gpt_image_25_model(image_input=True)
        model["output_type"] = "video"

        capability = main.public_runninghub_model_capabilities(model)

        self.assertNotIn("auto", [option["value"] for option in self._ratio_field(capability)["options"]])

    def test_adaptive_ratio_option_is_omitted_from_the_request(self):
        # Auto 在请求侧等价于“不发送 aspectRatio”，且不能被 schema 默认值补回。
        capability = main.public_runninghub_model_capabilities(self._gpt_image_25_model(image_input=True))
        params = [{
            "fieldKey": "aspectRatio",
            "type": "LIST",
            "required": False,
            "defaultValue": "16:9",
            "options": self._ratio_field(capability)["options"],
        }, {
            "fieldKey": "resolution",
            "type": "LIST",
            "defaultValue": "1k",
            "options": [{"value": "1k"}],
        }]
        body = {"prompt": "edit", "imageUrls": ["https://example.invalid/input.png"]}

        skipped = main.runninghub_apply_image_aspect(body, params, "", "1024x1024")
        main.runninghub_apply_schema_defaults(body, params, skip_keys=skipped)

        self.assertNotIn("aspectRatio", body)
        self.assertEqual(body["resolution"], "1k")

    def test_literal_auto_is_never_sent_to_runninghub(self):
        # 上游对 aspectRatio="auto" 返回 1007（不在允许值内），任何客户端传入
        # 字面量 auto 都必须收敛成“不发送该字段”。
        params = [{
            "fieldKey": "aspectRatio",
            "type": "LIST",
            "required": False,
            "defaultValue": "16:9",
            "options": [{"value": "1:1"}, {"value": "16:9"}],
        }]
        body = {"prompt": "edit"}

        skipped = main.runninghub_apply_image_aspect(body, params, "auto", "1024x1024")
        main.runninghub_apply_schema_defaults(body, params, skip_keys=skipped)

        self.assertNotIn("aspectRatio", body)


if __name__ == "__main__":
    unittest.main()
