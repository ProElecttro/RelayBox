import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from rq.exceptions import NoSuchJobError

import jobs_api


class JobsAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(jobs_api.app)

    @patch.object(jobs_api.redis_conn, "ping")
    def test_health(self, mock_ping):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["redis"], "connected")

    @patch.object(jobs_api.queue, "enqueue")
    def test_submit_probe(self, mock_enqueue):
        job = MagicMock()
        job.id = "test-job-123"
        mock_enqueue.return_value = job

        response = self.client.post(
            "/probes",
            json={
                "target": "https://example.com",
                "timeout": 5,
                "max_redirects": 5,
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "test-job-123")
        self.assertEqual(response.json()["status"], "queued")
        mock_enqueue.assert_called_once()

    def test_reject_http_target(self):
        response = self.client.post(
            "/probes",
            json={"target": "http://example.com"},
        )

        self.assertEqual(response.status_code, 400)

    def test_reject_invalid_timeout(self):
        response = self.client.post(
            "/probes",
            json={"target": "https://example.com", "timeout": 0},
        )

        self.assertEqual(response.status_code, 422)

    @patch.object(jobs_api.Job, "fetch", side_effect=NoSuchJobError("missing"))
    def test_unknown_job_returns_404(self, mock_fetch):
        response = self.client.get("/probes/nonexistent-job")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
