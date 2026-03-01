# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re

import requests
from bs4 import BeautifulSoup

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import clear_hostname_issue, mark_hostname_issue
from quasarr.providers.log import debug, info


class Source(AbstractDownloadSource):
    initials = "mb"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        MB source handler - fetches protected download links from MB pages.
        """
        headers = {
            "User-Agent": shared_state.values["user_agent"],
        }

        try:
            r = requests.get(
                url,
                headers=headers,
                timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
        except Exception as e:
            info(f"Failed to fetch page for {title or url}: {e}")
            mark_hostname_issue(Source.initials, "download", str(e))
            return {"links": []}

        soup = BeautifulSoup(r.text, "html.parser")

        download_links = []

        pattern = re.compile(
            r"https?://(?:www\.)?filecrypt\.[^/]+/Container/", re.IGNORECASE
        )
        for a in soup.find_all("a", href=pattern):
            try:
                link = a["href"]
                hoster = a.get_text(strip=True).lower()

                if mirrors and not any(m.lower() in hoster.lower() for m in mirrors):
                    debug(
                        f'Skipping link from "{hoster}" (not in desired mirrors "{mirrors}")!'
                    )
                    continue

                download_links.append([link, hoster])
            except Exception as e:
                debug(f"Error parsing download links: {e}")

        if not download_links:
            info(
                f"No download links found for {title}. Site structure may have changed. - {url}"
            )
            mark_hostname_issue(
                Source.initials,
                "download",
                "No download links found - site structure may have changed",
            )
            return {"links": []}

        clear_hostname_issue(Source.initials)
        return {"links": download_links}
