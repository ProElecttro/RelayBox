import json
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROBE = PROJECT_ROOT / "probe.py"


class PathLensCLITests(unittest.TestCase):
    def run_probe(self, *args):
        return subprocess.run(
            [sys.executable, str(PROBE), *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_invalid_timeout_is_rejected(self):
        result = self.run_probe("example.com", "--timeout", "0")
        self.assertEqual(result.returncode, 2)

    def test_negative_redirect_limit_is_rejected(self):
        result = self.run_probe("example.com", "--max-redirects", "-1")
        self.assertEqual(result.returncode, 2)

    def test_unsupported_http_url_returns_json(self):
        result = self.run_probe("http://example.com")
        report = json.loads(result.stdout)
        self.assertEqual(report["overall_outcome"], "FAILURE")
        self.assertEqual(report["hops"][0]["outcome"], "FAILURE")

    def test_verbose_output_does_not_corrupt_json(self):
        result = self.run_probe("example.com", "--verbose")
        report = json.loads(result.stdout)
        self.assertIn("overall_outcome", report)
        self.assertIsInstance(report["hops"], list)


if __name__ == "__main__":
    unittest.main()
