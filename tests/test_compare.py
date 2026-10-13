import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "compare.py"


class CompareTests(unittest.TestCase):
    def test_comparison_reports_stage_timings(self):
        runs = [
            {
                "hops": [{
                    "results": [
                        {"stage": "TCP_CONNECT", "elapsed_ms": value}
                    ]
                }]
            }
            for value in (10, 12, 20, 24)
        ]

        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history.jsonl"
            history.write_text(
                "".join(json.dumps(run) + "\n" for run in runs),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--history", str(history), "--last", "4"],
                capture_output=True,
                text=True,
                check=True,
            )

        self.assertIn("TCP_CONNECT", result.stdout)
        self.assertIn("REGRESSION", result.stdout)
        self.assertIn("Potential regressions: 1", result.stdout)


if __name__ == "__main__":
    unittest.main()
