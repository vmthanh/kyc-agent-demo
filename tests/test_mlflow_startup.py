"""Best-effort MLflow tracing must never block runtime startup."""
import os
import time
import unittest
from unittest.mock import patch

from app import api


class MLflowStartupTests(unittest.TestCase):
    def test_unreachable_tracking_server_is_skipped_quickly(self) -> None:
        with patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:9"}), \
                patch("mlflow.set_experiment") as set_experiment, \
                self.assertLogs("app.api", level="WARNING") as logs:
            started = time.perf_counter()
            api._enable_mlflow_autologging()
        self.assertLess(time.perf_counter() - started, 3.0)
        set_experiment.assert_not_called()
        self.assertIn("not reachable", "\n".join(logs.output))

    def test_reachable_tracking_server_enables_autolog(self) -> None:
        with patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001"}), \
                patch("app.api._tracking_server_reachable", return_value=True), \
                patch("mlflow.set_tracking_uri"), patch("mlflow.set_experiment") as set_experiment, \
                patch("mlflow.langchain.autolog") as autolog:
            api._enable_mlflow_autologging()
        set_experiment.assert_called_once()
        autolog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
