import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver

from app.checkpointing import MEMORY, MEMORY_DEGRADED, make_checkpointer


class MakeCheckpointerTests(unittest.TestCase):
    def test_blank_url_uses_memory_without_warning(self) -> None:
        with ExitStack() as stack:
            saver, backend = make_checkpointer(stack, redis_url="")
        self.assertIsInstance(saver, MemorySaver)
        self.assertEqual(backend, MEMORY)

    def test_unreachable_redis_falls_back_and_warns_about_durability(self) -> None:
        with self.assertLogs("app.checkpointing", level="WARNING") as logs:
            with ExitStack() as stack:
                saver, backend = make_checkpointer(stack, redis_url="redis://127.0.0.1:1")
        self.assertIsInstance(saver, MemorySaver)
        self.assertEqual(backend, MEMORY_DEGRADED)
        # The operator must be able to tell durability was lost, not just that
        # something failed -- this fallback is the known silent-misconfig risk.
        self.assertIn("NOT survive", "\n".join(logs.output))

    def test_unset_environment_variable_uses_memory(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with ExitStack() as stack:
                saver, backend = make_checkpointer(stack)
        self.assertEqual(backend, MEMORY)

    def test_environment_variable_is_read_when_no_argument_is_given(self) -> None:
        with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:1"}, clear=True):
            with self.assertLogs("app.checkpointing", level="WARNING"):
                with ExitStack() as stack:
                    _, backend = make_checkpointer(stack)
        self.assertEqual(backend, MEMORY_DEGRADED)


if __name__ == "__main__":
    unittest.main()
