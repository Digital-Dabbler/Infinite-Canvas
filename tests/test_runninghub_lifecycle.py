import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class RunningHubLifecycleTests(unittest.TestCase):
    def test_provider_declared_terminal_and_pending_states_are_classified(self):
        self.assertEqual(main.runninghub_poll_state({"data": {"status": "SUCCESS"}}), "succeeded")
        self.assertEqual(main.runninghub_poll_state({"status": "FAILED"}), "failed")
        self.assertEqual(main.runninghub_poll_state({"taskStatus": "RUNNING"}), "pending")
        self.assertEqual(main.runninghub_poll_state({"state": "QUEUED"}), "pending")

    def test_unknown_or_malformed_responses_are_indeterminate(self):
        self.assertEqual(main.runninghub_poll_state({"status": "SOMETHING_NEW"}), "indeterminate")
        self.assertEqual(main.runninghub_poll_state({"data": {}}), "indeterminate")
        self.assertEqual(main.runninghub_poll_state(None), "indeterminate")

    def test_recoverable_poll_error_keeps_task_id_separate_from_failure_status(self):
        error = main.runninghub_recoverable_poll_error(502, "状态暂无法确认", "task-123")
        self.assertEqual(error.status_code, 502)
        self.assertEqual(error.upstream_task_id, "task-123")
        self.assertTrue(error.recovery_available)


if __name__ == "__main__":
    unittest.main()
