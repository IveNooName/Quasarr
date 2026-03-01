# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS


def _normalize_mirror_name(mirror_name):
    normalized = str(mirror_name).lower().strip()

    if "://" in normalized:
        parsed = urlparse(normalized)
        normalized = parsed.netloc or parsed.path

    if normalized.startswith("www."):
        normalized = normalized[4:]

    normalized = normalized.split("/", 1)[0]
    normalized = normalized.split(":", 1)[0]
    if " " in normalized:
        normalized = normalized.split()[-1]
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]

    aliases = {
        "ddl": "ddownload",
        "ddlto": "ddownload",
        "rg": "rapidgator",
        "tb": "turbobit",
    }
    return aliases.get(normalized, normalized)


def _normalize_release_name(release_name):
    normalized = unquote(str(release_name)).lower().strip()
    # Treat punctuation/separators as equivalent so URL slugs and API names align.
    normalized = re.sub(r"[\W_]+", ".", normalized)
    return normalized.strip(".")


def _fetch_release_hosters(url, user_agent, release_title):
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"User-Agent": user_agent}

    page_response = requests.get(
        url,
        headers=headers,
        timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
    )
    page_response.raise_for_status()
    soup = BeautifulSoup(page_response.text, "html.parser")

    release_list_node = soup.find("div", {"id": "v-release-list"})
    if not release_list_node:
        return None

    media_id = release_list_node.get("data-mediaid")
    if not media_id:
        return None

    target_release = _normalize_release_name(release_title)
    if not target_release:
        return None

    api_response = requests.get(
        f"{base_url}/api/media/{media_id}/releases",
        headers=headers,
        timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
    )
    api_response.raise_for_status()
    release_map = api_response.json()

    for season_data in release_map.values():
        items = season_data.get("items", []) if isinstance(season_data, dict) else []
        for release_item in items:
            item_name = _normalize_release_name(release_item.get("name", ""))
            if item_name != target_release:
                continue
            hosters = release_item.get("hoster", [])
            return {
                _normalize_mirror_name(hoster)
                for hoster in hosters
                if _normalize_mirror_name(hoster)
            }

    return set()


def _release_matches_requested_mirrors(url, mirrors, user_agent, release_title):
    requested_mirrors = {
        _normalize_mirror_name(mirror) for mirror in (mirrors or []) if mirror
    }
    if not requested_mirrors:
        return True

    available_hosters = _fetch_release_hosters(url, user_agent, release_title)
    if not available_hosters:
        return False

    return bool(requested_mirrors.intersection(available_hosters))
