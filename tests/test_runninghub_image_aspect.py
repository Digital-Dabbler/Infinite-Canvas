import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class RunningHubImageAspectTests(unittest.TestCase):
    def test_explicit_outpaint_dimensions_are_not_snapped(self):
        self.assertEqual(main.snap_size_to_multiple("1234x2345", 16), "1248x2352")
        payload = main.OnlineImageRequest(
            prompt="outpaint",
            size="1234x2345",
            model_params={"__preserve_outpaint_dimensions": True},
        )
        self.assertEqual(main.online_image_request_size(payload), "1234x2345")

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

    def test_optional_adaptive_aspect_is_preserved_for_outpaint(self):
        params = [{
            "fieldKey": "aspectRatio",
            "required": False,
            "options": [{"value": "source"}, {"value": "1:1"}],
        }]
        body = {"prompt": "outpaint"}

        skipped = main.runninghub_apply_image_aspect(
            body, params, "source", "1536x1024", preserve_adaptive=True,
        )
        main.runninghub_apply_schema_defaults(body, params, skip_keys=skipped)

        self.assertEqual(body["aspectRatio"], "source")

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


if __name__ == "__main__":
    unittest.main()
