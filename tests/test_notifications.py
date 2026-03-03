# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace

from quasarr.providers.notifications.helpers.message_builder import (
    build_notification_message,
)
from quasarr.providers.notifications.helpers.notification_types import NotificationType


class NotificationMessageBuilderTests(unittest.TestCase):
    def setUp(self):
        self.shared_state = SimpleNamespace(values={})

    def test_failed_notification_includes_reason_entry(self):
        message = build_notification_message(
            self.shared_state,
            "Example.Release",
            NotificationType.FAILED,
            details={"reason": "All final download links were rejected."},
        )

        self.assertIsNotNone(message)
        self.assertEqual("Package marked as failed.", message.description)
        self.assertEqual(1, len(message.entries))
        self.assertEqual("Reason", message.entries[0].title)
        self.assertEqual(
            "All final download links were rejected.",
            message.entries[0].value,
        )

    def test_disabled_notification_uses_error_as_reason(self):
        message = build_notification_message(
            self.shared_state,
            "Example.Release",
            NotificationType.DISABLED,
            details={"error": "SponsorsHelper hit its retry limit."},
        )

        self.assertIsNotNone(message)
        self.assertEqual(1, len(message.entries))
        self.assertEqual("Reason", message.entries[0].title)
        self.assertEqual(
            "SponsorsHelper hit its retry limit.",
            message.entries[0].value,
        )


if __name__ == "__main__":
    unittest.main()
