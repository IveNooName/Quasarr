# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import mark_hostname_issue
from quasarr.providers.log import info


class Source(AbstractDownloadSource):
    initials = "nk"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        NK source handler - fetches protected download links from NK pages.
        """
        requested_mirrors = {
            _normalize_mirror_name(mirror) for mirror in (mirrors or []) if mirror
        }

        host = shared_state.values["config"]("Hostnames").get(Source.initials)
        headers = {
            "User-Agent": shared_state.values["user_agent"],
        }

        session = requests.Session()

        try:
            r = session.get(
                url,
                headers=headers,
                timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            info(f"Could not fetch release page for {title}: {e}")
            mark_hostname_issue(
                Source.initials,
                "download",
                str(e) if "e" in dir() else "Download error",
            )
            return {"links": []}

        anchors = soup.select("a.btn-orange")
        candidates = []
        for a in anchors:
            mirror = _normalize_mirror_name(a.text.strip())

            if mirror not in _SUPPORTED_MIRRORS:
                continue

            if requested_mirrors and mirror not in requested_mirrors:
                continue

            href = a.get("href", "").strip()
            if not href.lower().startswith(("http://", "https://")):
                href = "https://" + host + href

            if _is_nk_redirect_link(href, host):
                resolved_href = _resolve_nk_redirect(session, href, headers, title)
                if resolved_href:
                    href = resolved_href

            candidates.append([href, mirror])

        if not candidates:
            info(f"No external download links found for {title}")

        return {"links": candidates}


_SUPPORTED_MIRRORS = {"rapidgator", "ddownload"}


def _normalize_host(host):
    normalized = (host or "").lower().strip()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


def _is_nk_redirect_link(url, host):
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if _normalize_host(parsed.netloc) != _normalize_host(host):
        return False

    return parsed.path.startswith("/go/")


def _resolve_nk_redirect(session, url, headers, title):
    try:
        response = session.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        if "/404.html" in response.url:
            info(f"NK redirect resolved to 404 for {title}: {response.url}")
            return None

        return response.url
    except Exception as e:
        info(f"Could not resolve NK redirect for {title}: {e}")
        return None


def _normalize_mirror_name(mirror_name):
    normalized = mirror_name.lower().strip()

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
    }
    return aliases.get(normalized, normalized)
