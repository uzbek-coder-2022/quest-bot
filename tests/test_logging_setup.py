from __future__ import annotations

import logging
import tempfile
import unittest

from quest_bot.logging_setup import configure_logging


class FileLoggingTests(unittest.TestCase):
    def test_runtime_and_error_logs_are_written_and_secrets_redacted(self) -> None:
        root_logger = logging.getLogger()
        previous_handlers = root_logger.handlers[:]
        previous_level = root_logger.level
        with tempfile.TemporaryDirectory() as directory:
            try:
                log_path, error_path = configure_logging(
                    directory, extra_secrets=("private-token", "db-password")
                )
                test_logger = logging.getLogger("test.logging_setup")
                test_logger.info("Update received; token=%s", "private-token")
                try:
                    raise RuntimeError("database rejected db-password")
                except RuntimeError:
                    test_logger.exception("A test failure")

                for handler in root_logger.handlers:
                    handler.flush()

                runtime_text = log_path.read_text(encoding="utf-8")
                error_text = error_path.read_text(encoding="utf-8")
                self.assertIn("Update received", runtime_text)
                self.assertIn("A test failure", runtime_text)
                self.assertIn("A test failure", error_text)
                self.assertNotIn("private-token", runtime_text)
                self.assertNotIn("db-password", runtime_text)
                self.assertNotIn("private-token", error_text)
                self.assertNotIn("db-password", error_text)
                self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(error_path.stat().st_mode & 0o777, 0o600)
            finally:
                for handler in root_logger.handlers[:]:
                    root_logger.removeHandler(handler)
                    handler.close()
                root_logger.setLevel(previous_level)
                for handler in previous_handlers:
                    root_logger.addHandler(handler)


if __name__ == "__main__":
    unittest.main()
