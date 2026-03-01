# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from urllib.parse import urlparse

import requests

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import mark_hostname_issue
from quasarr.providers.log import info
from quasarr.providers.sessions.nx import retrieve_and_validate_session


class Source(AbstractDownloadSource):
    initials = "nx"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        NX source handler - auto-decrypts via site API and returns plain download links.
        """
        requested_mirrors = {
            _normalize_mirror_name(mirror) for mirror in (mirrors or []) if mirror
        }
        if requested_mirrors and "filer" not in requested_mirrors:
            info(f"No requested mirrors are available for {title}")
            return {"links": []}

        nx = shared_state.values["config"]("Hostnames").get(Source.initials)

        if f"{nx}/release/" not in url:
            info("Link is not a Release link, could not proceed:" + url)

        nx_session = retrieve_and_validate_session(shared_state)
        if not nx_session:
            info(f"Could not retrieve valid session for {nx}")
            mark_hostname_issue(Source.initials, "download", "Session error")
            return {"links": []}

        headers = {"User-Agent": shared_state.values["user_agent"], "Referer": url}

        json_data = {}

        url_segments = url.split("/")
        payload_url = "/".join(url_segments[:-2]) + "/api/getLinks/" + url_segments[-1]

        try:
            r = nx_session.post(
                payload_url,
                headers=headers,
                json=json_data,
                timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
            )
            r.raise_for_status()

            payload = r.json()
        except Exception as e:
            info(f"Could not get download links: {e}")
            mark_hostname_issue(Source.initials, "download", str(e))
            return {"links": []}

        if payload and any(key in payload for key in ("err", "error")):
            error_msg = payload.get("err") or payload.get("error")
            info(f"Error decrypting {title!r} URL: {url!r} - {error_msg}")
            mark_hostname_issue(Source.initials, "download", "Download error")
            shared_state.values["database"]("sessions").delete("nx")
            return {"links": []}

        try:
            decrypted_url = payload["link"][0]["url"]
            if decrypted_url:
                if not _is_filer_url(decrypted_url):
                    info(f"Unexpected non-filer mirror from NX for {title}")
                    return {"links": []}

                if _is_filer_folder_url(decrypted_url):
                    urls = _get_filer_folder_links_via_api(shared_state, decrypted_url)
                else:
                    urls = [decrypted_url]

                # Convert to [[url, mirror], ...] format
                links = [[u, _derive_mirror_from_url(u)] for u in urls]
                return {"links": links}
        except:
            pass

        info("Something went wrong decrypting " + str(title) + " URL: " + str(url))
        shared_state.values["database"]("sessions").delete("nx")
        return {"links": []}


def _derive_mirror_from_url(url):
    """Extract hoster name from URL hostname."""
    try:
        hostname = urlparse(url).netloc.lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        parts = hostname.split(".")
        if len(parts) >= 2:
            return parts[-2]
        return hostname
    except:
        return "unknown"


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
        "filernet": "filer",
    }
    return aliases.get(normalized, normalized)


def _is_filer_url(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        provider = host.split(".", 1)[0]
        return provider == "filer"
    except Exception:
        return False


def _is_filer_folder_url(url):
    try:
        parsed = urlparse(url)
        return _is_filer_url(url) and "/folder/" in parsed.path
    except Exception:
        return False


def _get_filer_folder_links_via_api(shared_state, url):
    try:
        headers = {"User-Agent": shared_state.values["user_agent"], "Referer": url}
        parsed = urlparse(url)
        api_base = f"{parsed.scheme}://{parsed.netloc}"

        m = re.search(r"/folder/([A-Za-z0-9]+)", url)
        if not m:
            return [url]

        folder_hash = m.group(1)
        api_url = f"{api_base}/api/folder/{folder_hash}"

        r = requests.get(
            api_url,
            headers=headers,
            timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
        )
        r.raise_for_status()

        data = r.json()
        files = data.get("files", [])
        links = []

        for f in files:
            file_hash = f.get("hash")
            if not file_hash:
                continue
            dl_url = f"{api_base}/get/{file_hash}"
            links.append(dl_url)

        return links if links else [url]

    except:
        return [url]
