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
    initials = "dw"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        DW source handler - fetches protected download links from DW site.
        """
        dw = shared_state.values["config"]("Hostnames").get(Source.initials)
        ajax_url = "https://" + dw + "/wp-admin/admin-ajax.php"

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
            content = BeautifulSoup(r.text, "html.parser")
            download_buttons = content.find_all("button", {"class": "show_link"})
        except Exception as e:
            info(
                f"Site has been updated. Grabbing download links for {title} not possible!"
            )
            mark_hostname_issue(Source.initials, "download", str(e))
            return {"links": []}

        download_links = []
        try:
            for button in download_buttons:
                payload = f"action=show_link&link_id={button['value']}"
                headers = {
                    "User-Agent": shared_state.values["user_agent"],
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                }

                r = session.post(
                    ajax_url,
                    payload,
                    headers=headers,
                    timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
                )
                r.raise_for_status()

                response = r.json()
                link = response["data"].split(",")[0]

                if dw in link:
                    match = re.search(
                        r"https://" + dw + r"/azn/af\.php\?v=([A-Z0-9]+)(#.*)?", link
                    )
                    if match:
                        link = (
                            f"https://filecrypt.cc/Container/{match.group(1)}"
                            f".html{match.group(2) if match.group(2) else ''}"
                        )

                    hoster = (
                        button.nextSibling.img["src"].split("/")[-1].replace(".png", "")
                    )
                    hoster = (
                        "1fichier" if hoster.startswith("fichier") else hoster
                    )  # align with expected mirror name
                    if mirrors and not any(
                        m.lower() in hoster.lower() for m in mirrors
                    ):
                        debug(
                            f'Skipping link from "{hoster}" (not in desired mirrors "{mirrors}")!'
                        )
                        continue

                    download_links.append([link, hoster])
        except Exception as e:
            info(
                f"Site has been updated. Parsing download links for {title} not possible!"
            )
            mark_hostname_issue(Source.initials, "download", str(e))

        if download_links:
            clear_hostname_issue(Source.initials)
        return {"links": download_links}
