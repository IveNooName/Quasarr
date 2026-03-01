# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import concurrent.futures
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import mark_hostname_issue
from quasarr.providers.log import debug, info


class Source(AbstractDownloadSource):
    initials = "by"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        BY source handler - fetches protected download links from BY iframes.
        """
        by = shared_state.values["config"]("Hostnames").get(Source.initials)
        headers = {
            "User-Agent": shared_state.values["user_agent"],
        }

        mirrors_lower = [m.lower() for m in mirrors] if mirrors else []
        links = []

        try:
            r = requests.get(
                url,
                headers=headers,
                timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            frames = [
                iframe.get("src")
                for iframe in soup.find_all("iframe")
                if iframe.get("src")
            ]

            frame_urls = [src for src in frames if f"https://{by}" in src]
            if not frame_urls:
                debug(f"No iframe hosts found on {url} for {title}.")
                return []

            async_results = []

            def fetch(url):
                try:
                    rq = requests.get(
                        url,
                        headers=headers,
                        timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
                    )
                    rq.raise_for_status()
                    return rq.text, url
                except Exception as e:
                    info(f"Error fetching iframe URL: {url}")
                    mark_hostname_issue(Source.initials, "download", str(e))
                    return None, url

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                future_to_url = {executor.submit(fetch, url): url for url in frame_urls}
                for future in concurrent.futures.as_completed(future_to_url):
                    content, source = future.result()
                    if content:
                        async_results.append((content, source))

            url_hosters = []
            for content, _source in async_results:
                host_soup = BeautifulSoup(content, "html.parser")
                link = host_soup.find(
                    "a",
                    href=re.compile(
                        r"https?://(?:www\.)?(?:hide\.cx|filecrypt\.(?:cc|co|to))/container/"
                    ),
                )

                # Fallback to the old format
                if not link:
                    link = host_soup.find("a", href=re.compile(r"/go\.php\?"))

                if not link:
                    continue

                href = link["href"]
                link_hostname = link.text.strip().replace(" ", "")
                hostname_lower = link_hostname.lower()

                if mirrors_lower and not any(
                    m in hostname_lower for m in mirrors_lower
                ):
                    debug(
                        f'Skipping link from "{link_hostname}" (not in desired mirrors "{mirrors}")!'
                    )
                    continue

                url_hosters.append((href, link_hostname))

            def resolve_redirect(href_hostname):
                href, _hostname = href_hostname
                try:
                    rq = requests.get(
                        href,
                        headers=headers,
                        timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
                        allow_redirects=True,
                    )
                    rq.raise_for_status()
                    if "/404.html" in rq.url:
                        info(f"Link leads to 404 page: {r.url}")
                        return None
                    time.sleep(1)
                    return rq.url
                except Exception as e:
                    info(f"Error resolving link: {e}")
                    mark_hostname_issue(
                        Source.initials,
                        "download",
                        str(e) if "e" in dir() else "Download error",
                    )
                    return None

            for pair in url_hosters:
                resolved_url = resolve_redirect(pair)
                link_hostname = pair[1]

                if not link_hostname:
                    link_hostname = urlparse(resolved_url).hostname

                if (
                    resolved_url
                    and link_hostname
                    and link_hostname.startswith(
                        ("ddownload", "rapidgator", "turbobit", "filecrypt")
                    )
                ):
                    if "rapidgator" in link_hostname:
                        links.insert(0, [resolved_url, link_hostname])
                    else:
                        links.append([resolved_url, link_hostname])

        except Exception as e:
            info(f"Error loading download links: {e}")
            mark_hostname_issue(
                Source.initials,
                "download",
                str(e) if "e" in dir() else "Download error",
            )

        return {"links": links}
