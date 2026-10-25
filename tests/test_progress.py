from io import StringIO
import json
import unittest

from pocketpull.progress import Progress


class ProgressTests(unittest.TestCase):
    def test_terminal_handles_known_unknown_and_empty(self):
        output = StringIO()
        progress = Progress(stream=output)
        progress("a\nfile", 0, 1024)
        progress("a\nfile", 1024, 1024)
        progress("unknown", 50, None)
        progress("empty", 0, 0)
        progress.finish()
        self.assertIn("100%", output.getvalue())
        self.assertIn("1.0 KiB", output.getvalue())
        self.assertIn('"a\\nfile"', output.getvalue())

    def test_structured_progress_is_json_lines(self):
        output = StringIO()
        progress = Progress(stream=output, structured=True)
        progress("猫.jpg", 0, 10)
        progress("猫.jpg", 10, 10)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(records[-1]["bytes"], 10)
        self.assertEqual(records[-1]["total"], 10)
        self.assertEqual(records[-1]["name"], "猫.jpg")

    def test_disabled_progress_is_silent(self):
        output = StringIO()
        progress = Progress(enabled=False, stream=output)
        progress("file", 100, 100)
        progress.finish()
        self.assertEqual(output.getvalue(), "")
