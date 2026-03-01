# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from html import escape

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


def _escape_html_text(value):
    return escape(str(value), quote=False)


def _escape_html_attribute(value):
    return escape(str(value), quote=True)


def _build_solved_section(details):
    """Build Telegram HTML lines from solved-CAPTCHA details."""
    data = build_solved_data(details)
    if data is None:
        return []

    parts = []
    for solver in data.get("solvers", []):
        value_parts = []
        attempts = solver["attempts"] if solver.get("has_attempts") else 0
        value_parts.append(f"<b>Attempts:</b> {_escape_html_text(attempts)}")
        currency = solver.get("currency")
        currency_text = _escape_html_text(currency) if currency else None
        if solver.get("cost") is not None:
            cost_text = format_number(solver["cost"])
            value_parts.append(
                f"<b>Cost:</b> {_escape_html_text(cost_text)} {currency_text}"
                if currency_text
                else f"<b>Cost:</b> {_escape_html_text(cost_text)}"
            )
        if solver.get("balance") is not None:
            balance_text = format_balance(solver["balance"])
            value_parts.append(
                f"<b>Balance:</b> {_escape_html_text(balance_text)} {currency_text}"
                if currency_text
                else f"<b>Balance:</b> {_escape_html_text(balance_text)}"
            )
        if value_parts:
            parts.append(
                f"<b>{_escape_html_text(solver['solver_display'])}</b> "
                + " | ".join(value_parts)
            )

    if data.get("duration"):
        parts.append(f"<b>Duration</b> {_escape_html_text(data['duration'])}")

    return parts


def _build_text(shared_state, title, case, details, source):
    """Build an HTML-formatted Telegram message for the given notification case."""
    parts = [f"<b>{_escape_html_text(title)}</b>"]

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
        safe_captcha_url = _escape_html_attribute(captcha_url)
        parts.append(
            f'<b>Solve CAPTCHA</b> Open <a href="{safe_captcha_url}">this link</a>'
            " to solve the CAPTCHA."
        )
        if not shared_state.values.get("helper_active"):
            parts.append(
                f'<b>SponsorsHelper</b> <a href="{SPONSORS_HELPER_URL}">'
                "Sponsors get automated CAPTCHA solutions!</a>"
            )
    elif case == NotificationType.QUASARR_UPDATE:
        version = "latest"
        link = ""
        if isinstance(details, dict):
            version = details.get("version") or version
            link = details.get("link") or ""
        safe_version = _escape_html_text(version)
        parts.append(f"Please update to {safe_version} as soon as possible!")
        if link:
            safe_link = _escape_html_attribute(link)
            parts.append(
                f'<b>Release notes at: </b> <a href="{safe_link}">'
                f"GitHub.com: rix1337/Quasarr/{safe_version}</a>"
            )
    elif case == NotificationType.TEST:
        parts.append("This is a test notification from Quasarr UI configuration.")
    else:
        info(f"Unknown notification case: {case}")
        return None

    if source and source.startswith("http"):
        safe_source = _escape_html_attribute(source)
        parts.append(
            f'<b>Source</b> <a href="{safe_source}">View release details here</a>'
        )

    return "\n\n".join(parts)


def send(
    shared_state,
    title,
    case,
    details=None,
    source=None,
    image_url=None,
    silent=True,
):
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
            "disable_notification": bool(silent),
        }
    else:
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": bool(silent),
            "disable_web_page_preview": not bool(image_url),
        }

    response = requests.post(api_url, json=params)
    result = response.json() if response.status_code == 200 else {}
    if not result.get("ok"):
        description = result.get("description", response.status_code)
        info(f"Failed to send Telegram notification: {description}")
        return False
    return True
