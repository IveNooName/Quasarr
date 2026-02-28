# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import requests

from quasarr.providers.log import info
from quasarr.providers.notifications._helpers import (
    SPONSORS_HELPER_URL,
    build_solved_data,
    format_balance,
    format_number,
)
from quasarr.providers.notifications.notification_types import (
    NotificationType,
    normalize_notification_type,
)


def _build_solved_section(details):
    """Build Telegram HTML lines from solved-CAPTCHA details."""
    data = build_solved_data(details)
    if data is None:
        return []

    parts = []
    for solver in data.get("solvers", []):
        value_parts = []
        attempts = solver["attempts"] if solver.get("has_attempts") else 0
        value_parts.append(f"<b>Attempts:</b> {attempts}")
        currency = solver.get("currency")
        if solver.get("cost") is not None:
            cost_text = format_number(solver["cost"])
            value_parts.append(
                f"<b>Cost:</b> {cost_text} {currency}"
                if currency
                else f"<b>Cost:</b> {cost_text}"
            )
        if solver.get("balance") is not None:
            balance_text = format_balance(solver["balance"])
            value_parts.append(
                f"<b>Balance:</b> {balance_text} {currency}"
                if currency
                else f"<b>Balance:</b> {balance_text}"
            )
        if value_parts:
            parts.append(
                f"<b>{solver['solver_display']}</b> " + " | ".join(value_parts)
            )

    if data.get("duration"):
        parts.append(f"<b>Duration</b> {data['duration']}")

    return parts


def _build_text(shared_state, title, case, details, source):
    """Build an HTML-formatted Telegram message for the given notification case."""
    parts = [f"<b>{title}</b>"]

    if case == NotificationType.UNPROTECTED:
        parts.append("No CAPTCHA required. Links were added directly!")
    elif case == NotificationType.SOLVED:
        parts.append("CAPTCHA solved by SponsorsHelper!")
        parts.extend(_build_solved_section(details))
    elif case == NotificationType.FAILED:
        parts.append(
            "SponsorsHelper failed to solve the CAPTCHA! "
            "Package marked as failed for deletion."
        )
    elif case == NotificationType.DISABLED:
        parts.append(
            "SponsorsHelper failed to solve the CAPTCHA! "
            "Please solve it manually to proceed."
        )
    elif case == NotificationType.CAPTCHA:
        parts.append("Download will proceed, once the CAPTCHA has been solved.")
        captcha_url = f"{shared_state.values['external_address']}/captcha"
        parts.append(
            f'<b>Solve CAPTCHA</b> Open <a href="{captcha_url}">this link</a>'
            " to solve the CAPTCHA."
        )
        if not shared_state.values.get("helper_active"):
            parts.append(
                f'<b>SponsorsHelper</b> <a href="{SPONSORS_HELPER_URL}">'
                "Sponsors get automated CAPTCHA solutions!</a>"
            )
    elif case == NotificationType.QUASARR_UPDATE:
        parts.append(f"Please update to {details['version']} as soon as possible!")
        if details:
            parts.append(
                f'<b>Release notes at: </b> <a href="{details["link"]}">'
                f"GitHub.com: rix1337/Quasarr/{details['version']}</a>"
            )
    elif case == NotificationType.TEST:
        parts.append("This is a test notification from Quasarr UI configuration.")
    else:
        info(f"Unknown notification case: {case}")
        return None

    if source and source.startswith("http"):
        parts.append(f'<b>Source</b> <a href="{source}">View release details here</a>')

    return "\n\n".join(parts)


def send(shared_state, title, case, details=None, source=None, image_url=None):
    """Build and send a Telegram notification. Returns True on success."""
    notification_type = normalize_notification_type(case)
    if notification_type is None:
        info(f"Unknown notification case: {case}")
        return False

    bot_token = shared_state.values.get("telegram_bot_token")
    chat_id = shared_state.values.get("telegram_chat_id")
    if not bot_token or not chat_id:
        return False

    text = _build_text(shared_state, title, notification_type, details, source)
    if text is None:
        return False

    if image_url and len(text) <= 1024:
        api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        params = {
            "chat_id": chat_id,
            "photo": image_url,
            "caption": text,
            "parse_mode": "HTML",
        }
    else:
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not bool(image_url),
        }

    response = requests.post(api_url, json=params)
    result = response.json() if response.status_code == 200 else {}
    if not result.get("ok"):
        description = result.get("description", response.status_code)
        info(f"Failed to send Telegram notification: {description}")
        return False
    return True
