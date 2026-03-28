# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import mark_hostname_issue
from quasarr.providers.log import debug, info
from quasarr.providers.utils import detect_crypter_type


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
                else:
                    # Never yield unresolved NK "/go" links. They bypass protected-link
                    # classification and end up as broken direct links in JD.
                    continue

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


def _is_filecrypt_link(url):
    return detect_crypter_type(url) == "filecrypt"


def _resolve_nk_redirect(session, url, headers, title):
    current_url = url
    visited = set()

    # Resolve redirect chain manually. This avoids fetching FileCrypt itself,
    # which can return 403 for automated traffic even when URL is valid.
    for _hop in range(8):
        if current_url in visited:
            debug(f"Could not resolve NK redirect for {title}: redirect loop detected")
            return None
        visited.add(current_url)

        try:
            response = session.get(
                current_url,
                headers=headers,
                allow_redirects=False,
                timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            debug(f"Could not resolve NK redirect for {title}: {e}")
            return None
        except Exception as e:
            debug(f"Could not resolve NK redirect for {title}: {e}")
            return None

        location = (response.headers.get("Location") or "").strip()
        if location:
            next_url = urljoin(current_url, location)
            if _is_filecrypt_link(next_url):
                return next_url
            current_url = next_url
            continue

        final_url = (response.url or current_url).strip()
        if _is_filecrypt_link(final_url):
            return final_url

        if "/404.html" in final_url:
            debug(f"NK redirect resolved to 404 for {title}: {final_url}")
            return None

        if response.status_code >= 400:
            debug(
                f"Could not resolve NK redirect for {title}: HTTP {response.status_code} at {final_url}"
            )
            return None

        debug(
            f"Could not resolve NK redirect for {title}: no redirect target from {final_url}"
        )
        return None

    debug(f"Could not resolve NK redirect for {title}: exceeded redirect hop limit")
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
