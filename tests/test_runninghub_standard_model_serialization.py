import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class RunningHubStandardModelSerializationTests(unittest.TestCase):
    def test_seedance_conversion_slots_default_is_an_array(self):
        params = [
            {
                "fieldKey": "duration",
                "type": "LIST",
                "required": True,
                "defaultValue": "5",
                "options": [{"value": "4"}, {"value": "5"}],
            },
            {
                "fieldKey": "conversionSlots",
                "type": "LIST",
                "defaultValue": "all",
                "options": [{"value": "all"}, {"value": "firstFrameUrl"}],
            },
            {"fieldKey": "realPersonMode", "type": "BOOLEAN", "defaultValue": True},
            {"fieldKey": "seed", "type": "INT", "defaultValue": "-1"},
        ]
        body = {"prompt": "sing"}

        main.runninghub_apply_schema_defaults(body, params)

        self.assertEqual(body["duration"], "5")
        self.assertEqual(body["conversionSlots"], ["all"])
        self.assertTrue(body["realPersonMode"])
        self.assertEqual(body["seed"], -1)

    def test_image_model_params_preserve_enum_but_encode_conversion_slots(self):
        fields = [
            {"fieldKey": "duration", "type": "LIST", "options": [{"value": "5"}]},
            {
                "fieldKey": "conversionSlots",
                "type": "LIST",
                "options": [{"value": "all"}, {"value": "firstFrameUrl"}],
            },
        ]

        result = main.runninghub_image_model_params(
            fields,
            {"duration": "5", "conversionSlots": "firstFrameUrl"},
        )

        self.assertEqual(result, {"duration": "5", "conversionSlots": ["firstFrameUrl"]})

    def test_multiple_media_uses_declared_cardinality_not_field_name(self):
        self.assertTrue(main.runninghub_schema_is_multiple({"fieldKey": "referenceImage", "multiple": True}))
        self.assertTrue(main.runninghub_schema_is_multiple({"fieldKey": "source", "maxCount": 3}))
        self.assertTrue(main.runninghub_schema_is_multiple({"fieldKey": "input", "maxInputNum": 2}))
        self.assertFalse(main.runninghub_schema_is_multiple({"fieldKey": "imageUrls", "maxInputNum": 1}))

    def test_public_capabilities_expose_declared_media_cardinality(self):
        result = main.public_runninghub_model_capabilities({
            "params": [{"fieldKey": "referenceImage", "type": "IMAGE", "multiple": True, "maxCount": 3}],
        })

        self.assertTrue(result["fields"][0]["multiple"])
        self.assertEqual(result["fields"][0]["maxCount"], 3)


if __name__ == "__main__":
    unittest.main()
