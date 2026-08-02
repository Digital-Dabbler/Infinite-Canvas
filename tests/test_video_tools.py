import os
import tempfile
import unittest
from unittest.mock import patch

import main


class VideoToolsTests(unittest.TestCase):
    def test_video_trim_request_rejects_invalid_range_values(self):
        with self.assertRaises(Exception):
            main.VideoTrimRequest(url="/assets/output/test.mp4", start=-1, end=2)
        with self.assertRaises(Exception):
            main.VideoTrimRequest(url="/assets/output/test.mp4", start=0, end=0)

    def test_trim_local_video_reports_missing_ffmpeg(self):
        with patch.object(main.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "FFmpeg"):
                main.trim_local_video("input.mp4", "output.mp4", 0, 1)

    def test_trim_local_video_builds_an_output_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "clip.mp4")

            def fake_run(command, **_kwargs):
                with open(command[-1], "wb") as output:
                    output.write(b"video")
                return type("Result", (), {"returncode": 0, "stderr": ""})()

            with patch.object(main.shutil, "which", return_value="ffmpeg"):
                with patch.object(main.subprocess, "run", side_effect=fake_run) as run:
                    main.trim_local_video("input.mp4", output_path, 1.25, 2.5, mute=True)
            self.assertTrue(os.path.isfile(output_path))
            command = run.call_args.args[0]
            self.assertIn("-ss", command)
            self.assertIn("-an", command)
            self.assertIn("-movflags", command)


if __name__ == "__main__":
    unittest.main()
