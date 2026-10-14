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



    @patch.object(jobs_api.queue, "enqueue")
    @patch.object(jobs_api.queue, "fetch_job")
    @patch.object(jobs_api.redis_conn, "set")
    @patch.object(jobs_api.redis_conn, "get", return_value=None)
    @patch.object(jobs_api.redis_conn, "lock")
    def test_recovers_job_when_idempotency_record_is_missing(
        self, mock_lock, mock_get, mock_set, mock_fetch, mock_enqueue
    ):
        lock_context = MagicMock()
        lock_context.__enter__.return_value = lock_context
        lock_context.__exit__.return_value = False
        mock_lock.return_value = lock_context

        existing_job = MagicMock()
        existing_job.id = "pathlens-existing-job"
        existing_job.args = ("https://example.com", 5.0, 5)
        existing_job.get_status.return_value = "queued"
        mock_fetch.return_value = existing_job

        response = self.client.post(
            "/probes",
            headers={"Idempotency-Key": "recovery-test"},
            json={
                "target": "https://example.com",
                "timeout": 5,
                "max_redirects": 5,
            },
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(
            response.json()["job_id"], "pathlens-existing-job"
        )
        self.assertTrue(response.json()["duplicate"])
        mock_enqueue.assert_not_called()
        mock_set.assert_called_once()

if __name__ == "__main__":
    unittest.main()
