# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.cloudflare import ensure_session_cf_bypassed
from quasarr.providers.hostname_issues import clear_hostname_issue, mark_hostname_issue
from quasarr.providers.log import debug, info


class Source(AbstractDownloadSource):
    initials = "sl"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        SL source handler - returns plain download links.
        """
        headers = {"User-Agent": shared_state.values["user_agent"]}
        session = requests.Session()

        try:
            session, headers, r = ensure_session_cf_bypassed(
                info,
                shared_state,
                session,
                url,
                headers,
                timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
            )
            if not r:
                raise requests.RequestException("Cloudflare bypass failed")
            r.raise_for_status()

            soup = BeautifulSoup(r.text, "html.parser")

            entry = soup.find("div", class_="entry")
            if not entry:
                info(f"Could not find main content section for {title}")
                mark_hostname_issue(
                    Source.initials, "download", "Could not find main content section"
                )
                return {"links": [], "imdb_id": None}

            imdb_id = None
            a_imdb = soup.find("a", href=re.compile(r"imdb\.com/title/tt\d+"))
            if a_imdb:
                m = re.search(r"(tt\d+)", a_imdb["href"])
                if m:
                    imdb_id = m.group(1)
                    debug(f"Found IMDb id: {imdb_id}")

            download_h2 = entry.find(
                lambda t: (
                    t.name == "h2" and "download" in t.get_text(strip=True).lower()
                )
            )
            if download_h2:
                anchors = []
                for sib in download_h2.next_siblings:
                    if getattr(sib, "name", None) == "h2":
                        break
                    if hasattr(sib, "find_all"):
                        anchors += sib.find_all("a", href=True)
            else:
                anchors = entry.find_all("a", href=True)

        except Exception as e:
            info(
                f"Site has been updated. Grabbing download links for {title} not possible! ({e})"
            )
            mark_hostname_issue(Source.initials, "download", str(e))
            return {"links": [], "imdb_id": None}

        filtered = []
        for a in anchors:
            href = a["href"].strip()
            if not href.lower().startswith(("http://", "https://")):
                continue

            host = (urlparse(href).hostname or "").lower()
            if not any(host.startswith(m + ".") for m in supported_mirrors):
                continue

            if mirrors and not any(m in href for m in mirrors):
                continue

            mirror_name = _derive_mirror_from_host(host)
            filtered.append([href, mirror_name])

        # regex fallback if still empty
        if not filtered:
            text = "".join(str(x) for x in anchors)
            urls = re.findall(r"https?://[^\s<>'\"]+", text)
            seen = set()
            for u in urls:
                u = u.strip()
                if u in seen:
                    continue
                seen.add(u)

                host = (urlparse(u).hostname or "").lower()
                if not any(host.startswith(m + ".") for m in supported_mirrors):
                    continue

                if mirrors and not any(m in u for m in mirrors):
                    continue

                mirror_name = _derive_mirror_from_host(host)
                filtered.append([u, mirror_name])

        if filtered:
            clear_hostname_issue(Source.initials)
        return {
            "links": filtered,
            "imdb_id": imdb_id,
        }


supported_mirrors = ["nitroflare", "ddownload"]


def _derive_mirror_from_host(host):
    """Get mirror name from hostname."""
    for m in supported_mirrors:
        if host.startswith(m + "."):
            return m
    return host.split(".")[0] if host else "unknown"
