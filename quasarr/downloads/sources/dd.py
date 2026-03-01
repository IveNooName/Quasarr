# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from quasarr.constants import DOWNLOAD_REQUEST_TIMEOUT_SECONDS
from quasarr.downloads.sources.helpers.abstract_source import AbstractDownloadSource
from quasarr.providers.hostname_issues import mark_hostname_issue
from quasarr.providers.log import debug, info
from quasarr.providers.sessions.dd import (
    create_and_persist_session,
    retrieve_and_validate_session,
)


class Source(AbstractDownloadSource):
    initials = "dd"

    def get_download_links(self, shared_state, url, mirrors, title, password):
        """
        Returns plain download links from DD API.
        """
        dd = shared_state.values["config"]("Hostnames").get(Source.initials)

        dd_session = retrieve_and_validate_session(shared_state)
        if not dd_session:
            info(f"Could not retrieve valid session for {dd}")
            mark_hostname_issue(Source.initials, "download", "Session error")
            return {"links": []}

        links = []

        qualities = [
            "disk-480p",
            "web-480p",
            "movie-480p-x265",
            "disk-1080p-x265",
            "web-1080p",
            "web-1080p-x265",
            "web-2160p-x265-hdr",
            "movie-1080p-x265",
            "movie-2160p-webdl-x265-hdr",
        ]

        headers = {
            "User-Agent": shared_state.values["user_agent"],
        }

        try:
            release_list = []
            for page in range(0, 100, 20):
                api_url = f"https://{dd}/index/search/keyword/{title}/qualities/{','.join(qualities)}/from/{page}/search"

                r = dd_session.get(
                    api_url,
                    headers=headers,
                    timeout=DOWNLOAD_REQUEST_TIMEOUT_SECONDS,
                )
                r.raise_for_status()
                releases_on_page = r.json()
                if releases_on_page:
                    release_list.extend(releases_on_page)

            for release in release_list:
                try:
                    if release.get("fake"):
                        debug(
                            f"Release {release.get('release')} marked as fake. "
                            "Invalidating session..."
                        )
                        create_and_persist_session(shared_state)
                        return {"links": []}
                    elif release.get("release") == title:
                        filtered_links = []
                        for link in release["links"]:
                            if mirrors and not any(
                                m in link["hostname"] for m in mirrors
                            ):
                                debug(
                                    f'Skipping link from "{link["hostname"]}" (not in desired mirrors "{mirrors}")!'
                                )
                                continue

                            if any(
                                existing_link["hostname"] == link["hostname"]
                                and existing_link["url"].endswith(".mkv")
                                and link["url"].endswith(".mkv")
                                for existing_link in filtered_links
                            ):
                                debug(
                                    f"Skipping duplicate `.mkv` link from {link['hostname']}"
                                )
                                continue
                            filtered_links.append(link)

                        # Build [[url, mirror], ...] format
                        links = [
                            [link["url"], link["hostname"]] for link in filtered_links
                        ]
                        break
                except Exception as e:
                    info(f"Error parsing download: {e}")
                    mark_hostname_issue(
                        Source.initials,
                        "download",
                        str(e) if "e" in dir() else "Download error",
                    )
                    continue

        except Exception as e:
            info(f"Error loading download: {e}")
            mark_hostname_issue(
                Source.initials,
                "download",
                str(e) if "e" in dir() else "Download error",
            )

        return {"links": links}
