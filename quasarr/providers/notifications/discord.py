# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json

import requests

from quasarr.providers.log import info
from quasarr.providers.notifications._helpers import (
    QUASARR_AVATAR,
    SPONSORS_HELPER_URL,
    build_solved_data,
    format_balance,
    format_number,
)
from quasarr.providers.notifications.notification_types import (
    NotificationType,
    normalize_notification_type,
)


def _build_solved_fields(details):
    """Build Discord embed fields from solved-CAPTCHA details."""
    data = build_solved_data(details)
    if data is None:
        return None

    fields = []
    for solver in data.get("solvers", []):
        value_parts = []
        attempts = solver["attempts"] if solver.get("has_attempts") else 0
        value_parts.append(f"**Attempts:** {attempts}")
        currency = solver.get("currency")
        if solver.get("cost") is not None:
            cost_text = format_number(solver["cost"])
            value_parts.append(
                f"**Cost:** {cost_text} {currency}"
                if currency
                else f"**Cost:** {cost_text}"
            )
        if solver.get("balance") is not None:
            balance_text = format_balance(solver["balance"])
            value_parts.append(
                f"**Balance:** {balance_text} {currency}"
                if currency
                else f"**Balance:** {balance_text}"
            )
        if value_parts:
            fields.append(
                {
                    "name": solver["solver_display"],
                    "value": " | ".join(value_parts),
                }
            )

    if data.get("duration"):
        fields.append({"name": "Duration", "value": data["duration"]})

    return fields or None


def _build_embed(shared_state, title, case, details, source, image_url):
    """Build a Discord embed dict for the given notification case."""
    if case == NotificationType.UNPROTECTED:
        description = "No CAPTCHA required. Links were added directly!"
        fields = None
    elif case == NotificationType.SOLVED:
        description = "CAPTCHA solved by SponsorsHelper!"
        fields = _build_solved_fields(details)
    elif case == NotificationType.FAILED:
        description = (
            "SponsorsHelper failed to solve the CAPTCHA! "
            "Package marked as failed for deletion."
        )
        fields = None
    elif case == NotificationType.DISABLED:
        description = (
            "SponsorsHelper failed to solve the CAPTCHA! "
            "Please solve it manually to proceed."
        )
        fields = None
    elif case == NotificationType.CAPTCHA:
        description = "Download will proceed, once the CAPTCHA has been solved."
        captcha_url = f"{shared_state.values['external_address']}/captcha"
        fields = [
            {
                "name": "Solve CAPTCHA",
                "value": f"Open [this link]({captcha_url}) to solve the CAPTCHA.",
            }
        ]
        if not shared_state.values.get("helper_active"):
            fields.append(
                {
                    "name": "SponsorsHelper",
                    "value": (
                        "[Sponsors get automated CAPTCHA solutions!]"
                        f"({SPONSORS_HELPER_URL})"
                    ),
                }
            )
    elif case == NotificationType.QUASARR_UPDATE:
        description = f"Please update to {details['version']} as soon as possible!"
        if details:
            fields = [
                {
                    "name": "Release notes at: ",
                    "value": (
                        f"[GitHub.com: rix1337/Quasarr/{details['version']}]"
                        f"({details['link']})"
                    ),
                }
            ]
        else:
            fields = None
    elif case == NotificationType.TEST:
        description = "This is a test notification from Quasarr UI configuration."
        fields = None
    else:
        info(f"Unknown notification case: {case}")
        return None

    if source and source.startswith("http"):
        if not fields:
            fields = []
        fields.append(
            {
                "name": "Source",
                "value": f"[View release details here]({source})",
            }
        )

    embed = {"title": title, "description": description}

    if fields:
        embed["fields"] = [{"name": f["name"], "value": f["value"]} for f in fields]

    if image_url:
        poster_object = {"url": image_url}
        embed["thumbnail"] = poster_object
        embed["image"] = poster_object
    elif case == NotificationType.QUASARR_UPDATE:
        embed["thumbnail"] = {"url": QUASARR_AVATAR}

    return embed


def send(shared_state, title, case, details=None, source=None, image_url=None):
    """Build and send a Discord webhook notification. Returns True on success."""
    notification_type = normalize_notification_type(case)
    if notification_type is None:
        info(f"Unknown notification case: {case}")
        return False

    webhook_url = shared_state.values.get("discord")
    if not webhook_url:
        return False

    embed = _build_embed(
        shared_state, title, notification_type, details, source, image_url
    )
    if embed is None:
        return False

    data = {
        "username": "Quasarr",
        "avatar_url": QUASARR_AVATAR,
        "embeds": [embed],
    }

    response = requests.post(
        webhook_url,
        data=json.dumps(data),
        headers={"Content-Type": "application/json"},
    )
    if response.status_code != 204:
        info(
            f"Failed to send message to Discord webhook. "
            f"Status code: {response.status_code}"
        )
        return False
    return True
