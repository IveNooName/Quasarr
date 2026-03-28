# -*- coding: utf-8 -*-

import json
import unittest

from quasarr.api.sponsors_helper import (
    normalize_helper_supported_urls,
    select_helper_package,
)


class SponsorsHelperApiTests(unittest.TestCase):
    def test_normalize_helper_supported_urls_deduplicates_and_lowercases(self):
        self.assertEqual(
            ["container.", "alpha.", "beta."],
            normalize_helper_supported_urls(
                [" Container. ", "ALPHA.", "", None, "beta.", "container."]
            ),
        )

    def test_select_helper_package_moves_supported_url_to_front(self):
        protected_packages = [
            (
                "pkg-1",
                json.dumps(
                    {
                        "title": "Example.Release",
                        "links": [
                            ["https://unsupported.invalid/path", "other"],
                            ["https://container.invalid/Container/abc", "container"],
                        ],
                        "password": "",
                    }
                ),
            )
        ]

        package_id, data, prioritized_links = select_helper_package(
            protected_packages,
            ["container."],
        )

        self.assertEqual("pkg-1", package_id)
        self.assertEqual("Example.Release", data["title"])
        self.assertEqual(
            "https://container.invalid/Container/abc",
            prioritized_links[0][0],
        )

    def test_select_helper_package_skips_unsupported_packages_until_match(self):
        protected_packages = [
            (
                "pkg-1",
                json.dumps(
                    {
                        "title": "Unsupported.First",
                        "links": [["https://unknown.invalid/path", "other"]],
                        "password": "",
                    }
                ),
            ),
            (
                "pkg-2",
                json.dumps(
                    {
                        "title": "Supported.Second",
                        "links": [["https://alpha.invalid/f/abc", "alpha"]],
                        "password": "",
                    }
                ),
            ),
        ]

        package_id, data, prioritized_links = select_helper_package(
            protected_packages,
            ["container.", "alpha."],
        )

        self.assertEqual("pkg-2", package_id)
        self.assertEqual("Supported.Second", data["title"])
        self.assertEqual("https://alpha.invalid/f/abc", prioritized_links[0][0])

    def test_select_helper_package_returns_none_when_nothing_matches(self):
        protected_packages = [
            (
                "pkg-1",
                json.dumps(
                    {
                        "title": "Unsupported.Only",
                        "links": [["https://unknown.invalid/path", "other"]],
                        "password": "",
                    }
                ),
            )
        ]

        self.assertIsNone(select_helper_package(protected_packages, ["container."]))


if __name__ == "__main__":
    unittest.main()
