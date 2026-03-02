# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from quasarr.providers.notifications.helpers.abstract_notification_formatter import (
    AbstractNotificationFormatter,
)
from quasarr.providers.notifications.helpers.common import (
    build_solved_data,
    canonicalize_solver_name,
    format_balance,
    format_duration,
    format_number,
    resolve_poster_url,
)
from quasarr.providers.notifications.helpers.message_builder import (
    build_notification_message,
)
from quasarr.providers.notifications.helpers.notification_message import (
    AbstractNotificationEntry,
    NotificationFact,
    NotificationFactsEntry,
    NotificationLinkEntry,
    NotificationMessage,
    NotificationTextEntry,
    NotificationValueEntry,
)
from quasarr.providers.notifications.helpers.notification_types import (
    NotificationType,
    get_notification_type_label,
    get_user_configurable_notification_types,
    normalize_notification_type,
)

__all__ = [
    "AbstractNotificationEntry",
    "AbstractNotificationFormatter",
    "NotificationFact",
    "NotificationFactsEntry",
    "NotificationLinkEntry",
    "NotificationMessage",
    "NotificationTextEntry",
    "NotificationType",
    "NotificationValueEntry",
    "build_notification_message",
    "build_solved_data",
    "canonicalize_solver_name",
    "format_balance",
    "format_duration",
    "format_number",
    "get_notification_type_label",
    "get_user_configurable_notification_types",
    "normalize_notification_type",
    "resolve_poster_url",
]
