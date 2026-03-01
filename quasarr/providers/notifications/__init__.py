# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from quasarr.providers.log import info
from quasarr.providers.notifications._helpers import resolve_poster_url
from quasarr.providers.notifications.notification_types import (
    NotificationType,
    normalize_notification_type,
)


def _provider_case_enabled(shared_state, provider, notification_type):
    toggles = shared_state.values.get("notification_toggles")
    if not isinstance(toggles, dict):
        return True

    provider_toggles = toggles.get(provider)
    if not isinstance(provider_toggles, dict):
        return True

    return bool(provider_toggles.get(notification_type.value, True))


def _provider_case_silent(shared_state, provider, notification_type):
    silent_settings = shared_state.values.get("notification_silent")
    if not isinstance(silent_settings, dict):
        return True

    provider_silent = silent_settings.get(provider)
    if not isinstance(provider_silent, dict):
        return True

    return bool(provider_silent.get(notification_type.value, True))


def send_notification(
    shared_state, title, case, imdb_id=None, details=None, source=None
):
    """
    Send a notification to all configured providers (Discord, Telegram).

    Each provider is attempted independently — a failure in one does not block others.

    :param shared_state: Shared state object containing configuration.
    :param title: Title of the notification.
    :param case: A string representing the scenario (e.g., 'captcha', 'failed', 'unprotected').
    :param imdb_id: A string starting with "tt" followed by at least 7 digits, representing an object on IMDb
    :param details: A dictionary containing additional details, such as version and link for updates.
    :param source: Optional source of the notification, sent as a field in the embed.
    :return: True if at least one provider sent successfully, False otherwise.
    """
    from quasarr.providers.notifications import discord, telegram

    notification_type = normalize_notification_type(case)
    if notification_type is None:
        info(f"Unknown notification case: {case}")
        return False

    has_discord = bool(shared_state.values.get("discord"))
    has_telegram = bool(shared_state.values.get("telegram_bot_token")) and bool(
        shared_state.values.get("telegram_chat_id")
    )

    if not has_discord and not has_telegram:
        return False

    # Resolve poster image once for all providers.
    image_url = None
    if notification_type in (NotificationType.UNPROTECTED, NotificationType.CAPTCHA):
        image_url = resolve_poster_url(shared_state, title, imdb_id)

    any_success = False

    if has_discord and _provider_case_enabled(
        shared_state, "discord", notification_type
    ):
        discord_silent = _provider_case_silent(
            shared_state, "discord", notification_type
        )
        try:
            if discord.send(
                shared_state,
                title,
                notification_type,
                details=details,
                source=source,
                image_url=image_url,
                silent=discord_silent,
            ):
                any_success = True
        except Exception as e:
            info(f"Discord notification error: {e}")

    if has_telegram and _provider_case_enabled(
        shared_state, "telegram", notification_type
    ):
        telegram_silent = _provider_case_silent(
            shared_state, "telegram", notification_type
        )
        try:
            if telegram.send(
                shared_state,
                title,
                notification_type,
                details=details,
                source=source,
                image_url=image_url,
                silent=telegram_silent,
            ):
                any_success = True
        except Exception as e:
            info(f"Telegram notification error: {e}")

    return any_success
