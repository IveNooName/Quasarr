# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json
import os
import re

import requests

from quasarr.constants import SUPPRESS_NOTIFICATIONS
from quasarr.providers.imdb_metadata import get_imdb_id_from_title, get_poster_link
from quasarr.providers.log import info

silent_env = os.getenv("SILENT", "")
silent = bool(silent_env)
silent_max = silent_env.lower() == "max"


def _canonicalize_solver_name(name):
    raw = str(name).strip() if name is not None else ""
    compact = raw.lower().replace(" ", "").replace("-", "").replace("_", "")
    if compact in {"2captcha", "2captchacom"}:
        return "2captcha", "2Captcha"
    if compact in {"dbc", "deathbycaptcha", "deathbycaptchacom"}:
        return "deathbycaptcha", "DeathByCaptcha"
    if not raw:
        return "unknown", "Unknown"
    return compact, raw


def _format_duration(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        # Already in HH:MM:SS-like format.
        if stripped.count(":") == 2:
            try:
                parts = [int(part) for part in stripped.split(":")]
                if len(parts) == 3:
                    hours, minutes, seconds = parts
                    total_seconds = max(0, (hours * 3600) + (minutes * 60) + seconds)
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    seconds = total_seconds % 60
                    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            except ValueError:
                return None
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[sS]?", stripped)
        if match:
            value = float(match.group(1))
        else:
            return None
    if isinstance(value, (int, float)):
        total_seconds = max(0, int(round(float(value))))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return None


def _format_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            return f"{value:.5f}".rstrip("0").rstrip(".")
        return str(value)
    return str(value)


def _format_balance(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _build_solved_fields(details):
    if not isinstance(details, dict):
        return None

    fields = []

    providers = details.get("solvers")
    grouped_providers = []
    grouped_provider_map = {}
    if isinstance(providers, list):
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            raw_name = provider.get("name")
            solver_key, solver_display = _canonicalize_solver_name(raw_name)
            if solver_key not in grouped_provider_map:
                grouped_provider_map[solver_key] = {
                    "solver_key": solver_key,
                    "solver_display": solver_display,
                    "attempts": 0,
                    "has_attempts": False,
                    "cost": None,
                    "balance": None,
                    "currency": None,
                }
                grouped_providers.append(grouped_provider_map[solver_key])

            grouped_provider = grouped_provider_map[solver_key]

            if provider.get("attempts") is not None:
                try:
                    grouped_provider["attempts"] += max(
                        0, int(provider.get("attempts"))
                    )
                    grouped_provider["has_attempts"] = True
                except (TypeError, ValueError):
                    pass
            if provider.get("cost") is not None:
                grouped_provider["cost"] = provider.get("cost")
            if provider.get("balance") is not None:
                grouped_provider["balance"] = provider.get("balance")
            if provider.get("currency"):
                grouped_provider["currency"] = provider.get("currency")

    if grouped_providers:
        preferred_order = {"2captcha": 0, "deathbycaptcha": 1}
        sorted_providers = sorted(
            grouped_providers,
            key=lambda provider: (
                preferred_order.get(provider.get("solver_key"), 99),
                provider.get("solver_key", ""),
            ),
        )
        for provider in sorted_providers:
            value_parts = []
            attempts = provider.get("attempts") if provider.get("has_attempts") else 0
            value_parts.append(f"**Attempts:** {attempts}")
            currency = provider.get("currency")
            if provider.get("cost") is not None:
                cost_text = _format_number(provider.get("cost"))
                value_parts.append(
                    f"**Cost:** {cost_text} {currency}"
                    if currency
                    else f"**Cost:** {cost_text}"
                )
            if provider.get("balance") is not None:
                balance_text = _format_balance(provider.get("balance"))
                value_parts.append(
                    f"**Balance:** {balance_text} {currency}"
                    if currency
                    else f"**Balance:** {balance_text}"
                )
            if value_parts:
                fields.append(
                    {
                        "name": provider["solver_display"],
                        "value": " | ".join(value_parts),
                    }
                )

    duration_value = details.get("duration_seconds")
    formatted_duration = _format_duration(duration_value)
    if formatted_duration:
        fields.append({"name": "Duration", "value": formatted_duration})

    return fields or None


def send_discord_message(
    shared_state, title, case, imdb_id=None, details=None, source=None
):
    """
    Sends a Discord message to the webhook provided in the shared state, based on the specified case.

    :param shared_state: Shared state object containing configuration.
    :param title: Title of the embed to be sent.
    :param case: A string representing the scenario (e.g., 'captcha', 'failed', 'unprotected').
    :param imdb_id: A string starting with "tt" followed by at least 7 digits, representing an object on IMDb
    :param details: A dictionary containing additional details, such as version and link for updates.
    :param source: Optional source of the notification, sent as a field in the embed.
    :return: True if the message was sent successfully, False otherwise.
    """
    if not shared_state.values.get("discord"):
        return False

    # SILENT=MAX blocks all Discord messages except explicit failure cases.
    if silent_max and case not in ["failed", "disabled"]:
        return True

    poster_object = None
    if case == "unprotected" or case == "captcha":
        if (
            not imdb_id and " " not in title
        ):  # this should prevent imdb_search for ebooks and magazines
            imdb_id = get_imdb_id_from_title(shared_state, title)
        if imdb_id:
            poster_link = get_poster_link(shared_state, imdb_id)
            if poster_link:
                poster_object = {"url": poster_link}

    # Decide the embed content based on the case
    if case == "unprotected":
        description = "No CAPTCHA required. Links were added directly!"
        fields = None
    elif case == "solved":
        description = "CAPTCHA solved by SponsorsHelper!"
        fields = _build_solved_fields(details)
    elif case == "failed":
        description = "SponsorsHelper failed to solve the CAPTCHA! Package marked as failed for deletion."
        fields = None
    elif case == "disabled":
        description = "SponsorsHelper failed to solve the CAPTCHA! Please solve it manually to proceed."
        fields = None
    elif case == "captcha":
        description = "Download will proceed, once the CAPTCHA has been solved."
        fields = [
            {
                "name": "Solve CAPTCHA",
                "value": f"Open [this link]({f'{shared_state.values["external_address"]}/captcha'}) to solve the CAPTCHA.",
            }
        ]
        if not shared_state.values.get("helper_active"):
            fields.append(
                {
                    "name": "SponsorsHelper",
                    "value": "[Sponsors get automated CAPTCHA solutions!](https://github.com/rix1337/Quasarr?tab=readme-ov-file#sponsorshelper)",
                }
            )
    elif case == "quasarr_update":
        description = f"Please update to {details['version']} as soon as possible!"
        if details:
            fields = [
                {
                    "name": "Release notes at: ",
                    "value": f"[GitHub.com: rix1337/Quasarr/{details['version']}]({details['link']})",
                }
            ]
        else:
            fields = None
    else:
        info(f"Unknown notification case: {case}")
        return False

    data = {
        "username": "Quasarr",
        "avatar_url": "https://raw.githubusercontent.com/rix1337/Quasarr/main/Quasarr.png",
        "embeds": [
            {
                "title": title,
                "description": description,
            }
        ],
    }

    if source and source.startswith("http"):
        if not fields:
            fields = []
        fields.append(
            {
                "name": "Source",
                "value": f"[View release details here]({source})",
            }
        )

    if fields:
        data["embeds"][0]["fields"] = fields

    if poster_object:
        data["embeds"][0]["thumbnail"] = poster_object
        data["embeds"][0]["image"] = poster_object
    elif case == "quasarr_update":
        data["embeds"][0]["thumbnail"] = {
            "url": "https://raw.githubusercontent.com/rix1337/Quasarr/main/Quasarr.png"
        }

    # Apply silent mode: suppress notifications for all non-error cases.
    if silent and case not in ["failed", "quasarr_update", "disabled"]:
        data["flags"] = SUPPRESS_NOTIFICATIONS

    response = requests.post(
        shared_state.values["discord"],
        data=json.dumps(data),
        headers={"Content-Type": "application/json"},
    )
    if response.status_code != 204:
        info(
            f"Failed to send message to Discord webhook. Status code: {response.status_code}"
        )
        return False

    return True
