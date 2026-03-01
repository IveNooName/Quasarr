# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import requests
from bs4 import BeautifulSoup

from quasarr.constants import (
    AFFILIATE_REGEX,
    DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
    FILECRYPT_REGEX,
)
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import mark_hostname_issue
from quasarr.providers.log import debug, info


class Source(AbstractDownloadSource):
    initials = "hs"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        HS handler - extracts filecrypt download links from release pages.
        The site structure pairs affiliate links (indicating mirror) with filecrypt links.
        """
        headers = {"User-Agent": shared_state.values["user_agent"]}

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

            # Find all links in the page
            all_links = soup.find_all("a", href=True)

            # Strategy: Build mirror detection from multiple sources
            # 1. Text labels on filecrypt links (most reliable)
            # 2. Affiliate links preceding filecrypt links (fallback)

            # First pass: detect mirrors from link text labels
            text_labeled_mirrors = {}  # url -> mirror_name
            for link in all_links:
                href = link.get("href", "")
                if FILECRYPT_REGEX.match(href):
                    link_text = link.get_text(strip=True).lower()
                    detected_mirror = None
                    if "ddownload" in link_text or "ddl" in link_text:
                        detected_mirror = "ddownload"
                    elif "rapidgator" in link_text:
                        detected_mirror = "rapidgator"
                    elif "katfile" in link_text:
                        detected_mirror = "katfile"
                    if detected_mirror:
                        text_labeled_mirrors[href] = detected_mirror

            # Second pass: track affiliate links for fallback
            affiliate_mirrors = {}  # url -> mirror_name (from preceding affiliate)
            current_mirror = None
            for link in all_links:
                href = link.get("href", "")

                # Check if this is an affiliate link (indicates mirror name)
                aff_match = AFFILIATE_REGEX.search(href)
                if aff_match:
                    current_mirror = _normalize_mirror_name(aff_match.group(1))
                    continue

                # Check if this is a filecrypt link
                if FILECRYPT_REGEX.match(href):
                    if current_mirror and href not in affiliate_mirrors:
                        affiliate_mirrors[href] = current_mirror
                    current_mirror = None  # Reset for next pair

            # Combine results: text labels take priority over affiliate tracking
            filecrypt_mirrors = []
            seen_urls = set()
            for link in all_links:
                href = link.get("href", "")
                if FILECRYPT_REGEX.match(href) and href not in seen_urls:
                    seen_urls.add(href)
                    # Priority: text label > affiliate > "filecrypt"
                    mirror_name = (
                        text_labeled_mirrors.get(href)
                        or affiliate_mirrors.get(href)
                        or "filecrypt"
                    )
                    filecrypt_mirrors.append((href, mirror_name))

            # Filter by requested mirror and deduplicate
            seen_urls = set()
            for fc_url, fc_mirror in filecrypt_mirrors:
                if fc_url in seen_urls:
                    continue
                seen_urls.add(fc_url)

                # Filter by requested mirror if specified
                if mirrors_lower:
                    if fc_mirror not in mirrors_lower:
                        debug(
                            f"Skipping {fc_mirror} link (requested mirrors: {mirrors})"
                        )
                        continue

                # Store [url, mirror_name] - mirror_name is used by CAPTCHA page for filtering
                links.append([fc_url, fc_mirror])

            if not links:
                debug(f"No filecrypt links found on {url} for {title}")

        except Exception as e:
            info(f"Error loading download links: {e}")
            mark_hostname_issue(Source.initials, "download", str(e))

        return {"links": links}


def _normalize_mirror_name(name):
    """Normalize mirror names - ddlto/ddl.to -> ddownload"""
    if not name:
        return None
    name_lower = name.lower().strip()
    if "ddlto" in name_lower or "ddl.to" in name_lower or "ddownload" in name_lower:
        return "ddownload"
    if "rapidgator" in name_lower:
        return "rapidgator"
    if "katfile" in name_lower:
        return "katfile"
    return name_lower
