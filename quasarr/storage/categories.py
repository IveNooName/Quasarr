# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json
import re

from quasarr.constants import (
    DOWNLOAD_CATEGORIES,
    SEARCH_CAT_SHOWS_ANIME,
    SEARCH_CAT_SHOWS_DOCUMENTARY,
    SEARCH_CATEGORIES,
)

from ..providers.log import info
from .sqlite_database import DataBase


def _normalize_search_sources(sources):
    if not isinstance(sources, list):
        return []

    normalized = []
    seen = set()
    for source in sources:
        if not isinstance(source, str):
            continue
        value = source.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    return normalized


def get_search_category_ui_heading(name):
    """
    Return UI-only heading text for search categories without changing API names.

    Standalone TV special categories are displayed without the "TV/" prefix
    in the categories web UI heading.
    """
    if not isinstance(name, str):
        return ""
    if name.startswith("TV/"):
        return name.split("/", 1)[1]
    return name


def get_download_categories():
    """Returns a sorted list of all category names, ensuring defaults exist."""
    db = DataBase("categories_download")

    # Ensure default categories always exist
    for cat in DOWNLOAD_CATEGORIES:
        data_str = db.retrieve(cat)
        if not data_str:
            # Persist only mutable settings in DB (e.g. mirrors), never static emoji.
            db.store(cat, json.dumps({}))
            info(f"Restored default category: {cat}")
            continue

        try:
            data = json.loads(data_str)
            if isinstance(data, dict) and "emoji" in data:
                data.pop("emoji", None)
                db.update_store(cat, json.dumps(data))
        except json.JSONDecodeError:
            db.update_store(cat, json.dumps({}))

    cats = db.retrieve_all_titles() or []
    for cat_tuple in cats:
        cat = cat_tuple[0]
        data_str = db.retrieve(cat)
        if not data_str:
            continue
        try:
            data = json.loads(data_str)
            if isinstance(data, dict) and "emoji" in data:
                data.pop("emoji", None)
                db.update_store(cat, json.dumps(data))
        except json.JSONDecodeError:
            if cat in DOWNLOAD_CATEGORIES:
                db.update_store(cat, json.dumps({}))

    return sorted([c[0] for c in cats])


def get_download_category_emoji(name):
    """Returns the emoji for a category."""
    # Emojis are static constants and intentionally not persisted in DB.
    return DOWNLOAD_CATEGORIES.get(name, {}).get("emoji", "📁")


def get_download_category_mirrors(name, lowercase=False):
    """Returns the list of preferred mirrors for a category."""
    db = DataBase("categories_download")
    data_str = db.retrieve(name)
    if data_str:
        try:
            data = json.loads(data_str)
            mirrors = data.get("mirrors", [])
            if lowercase:
                return [m.lower() for m in mirrors]
            return mirrors
        except json.JSONDecodeError:
            pass
    return []


def get_search_categories():
    """Returns a dictionary of all search categories (default + custom)."""
    db = DataBase("categories_search")

    # Ensure default categories always exist in DB (for whitelists)
    for cat_id, cat_info in SEARCH_CATEGORIES.items():
        cat_id = int(cat_id)
        data_str = db.retrieve(str(cat_id))
        if not data_str:
            # Persist only mutable settings in DB (e.g. search_sources), not static metadata.
            db.store(str(cat_id), json.dumps({}))
            info(f"Restored default search category: {cat_id} ({cat_info['name']})")
            continue

        try:
            data = json.loads(data_str)
            if not isinstance(data, dict):
                db.update_store(str(cat_id), json.dumps({}))
                continue

            sanitized_data = {}
            if "search_sources" in data:
                sanitized_data["search_sources"] = _normalize_search_sources(
                    data.get("search_sources", [])
                )
            if sanitized_data != data:
                db.update_store(str(cat_id), json.dumps(sanitized_data))
        except json.JSONDecodeError:
            db.update_store(str(cat_id), json.dumps({}))

    # Start with default categories
    categories = SEARCH_CATEGORIES.copy()

    # Add custom categories from DB
    custom_cats = db.retrieve_all_titles() or []
    for cat_id_tuple in custom_cats:
        cat_id = int(cat_id_tuple[0])
        data_str = db.retrieve(str(cat_id))
        if data_str:
            try:
                data = json.loads(data_str)
                if "name" not in data:
                    continue
                if "emoji" in data:
                    data.pop("emoji", None)
                    db.update_store(str(cat_id), json.dumps(data))

                base_type = data.get("base_type")
                try:
                    base_type = int(base_type)
                except (TypeError, ValueError):
                    if cat_id >= 100000:
                        base_type = cat_id - 100000
                    else:
                        base_type = cat_id

                base_cat_info = SEARCH_CATEGORIES.get(base_type, {})
                categories[cat_id] = {
                    "name": data["name"],
                    "emoji": base_cat_info.get("emoji", "📁"),
                    "base_type": base_type,
                    "search_sources": _normalize_search_sources(
                        data.get("search_sources", [])
                    ),
                }
            except json.JSONDecodeError:
                pass

    return categories


def get_search_category_sources(cat_id):
    """Returns the list of preferred search sources for a search category ID."""
    if cat_id is None or cat_id == "":
        return []
    try:
        cat_id = int(cat_id)
    except (ValueError, TypeError):
        return []

    owner_cat_id = get_search_category_whitelist_owner(cat_id)
    lookup_ids = [owner_cat_id]
    if owner_cat_id != cat_id:
        # Fall back to a category-specific row when ownership differs.
        lookup_ids.append(cat_id)

    db = DataBase("categories_search")
    for lookup_id in lookup_ids:
        data_str = db.retrieve(str(lookup_id))
        if not data_str:
            continue
        try:
            data = json.loads(data_str)
            normalized_sources = _normalize_search_sources(
                data.get("search_sources", [])
            )
            if normalized_sources != data.get("search_sources", []):
                data["search_sources"] = normalized_sources
                db.update_store(str(lookup_id), json.dumps(data))
            return normalized_sources
        except json.JSONDecodeError:
            continue
    return []


def get_search_category_whitelist_owner(cat_id: int) -> int:
    """
    Resolve which category ID owns whitelist settings.

    - Base categories own themselves.
    - Quality/format subcategories inherit from their base category.
    - TV/Anime (5070) is treated as its own base.
    - Custom categories (100000+) keep their own independent whitelist.
    """
    try:
        cat_id = int(cat_id)
    except (ValueError, TypeError):
        return cat_id

    if cat_id >= 100000:
        return cat_id

    if cat_id == SEARCH_CAT_SHOWS_ANIME:
        return cat_id

    if cat_id == SEARCH_CAT_SHOWS_DOCUMENTARY:
        return cat_id

    if cat_id % 1000 == 0:
        return cat_id

    base_cat_id = (cat_id // 1000) * 1000
    if base_cat_id in SEARCH_CATEGORIES:
        return base_cat_id

    return cat_id


def update_search_category_sources(cat_id: int, sources):
    """Updates the preferred search sources for a search category ID."""
    if not cat_id:
        return False, "Category ID is required."
    db = DataBase("categories_search")
    cat_id = int(cat_id)
    owner_cat_id = get_search_category_whitelist_owner(cat_id)
    sources = _normalize_search_sources(sources)
    is_default = owner_cat_id in SEARCH_CATEGORIES

    data_str = db.retrieve(str(owner_cat_id))
    data = {}

    if data_str:
        try:
            data = json.loads(data_str)
            if isinstance(data, dict):
                data.pop("emoji", None)
        except json.JSONDecodeError:
            return False, f"Database error for category id {owner_cat_id}."
    elif is_default:
        # Implicitly create default category if missing
        data = {}
    else:
        return False, f"Search category ID {owner_cat_id} not found."

    if is_default:
        data = (
            {"search_sources": data.get("search_sources", [])}
            if isinstance(data, dict)
            else {}
        )

    data["search_sources"] = sources
    db.update_store(str(owner_cat_id), json.dumps(data))

    info(
        f"Updated search-source-whitelist for search category {cat_id} (owner {owner_cat_id}) to {[source.upper() for source in sources]}"
    )
    return (
        True,
        f"Search category {cat_id} search-source-whitelist updated successfully.",
    )


def add_custom_search_category(base_cat_id: int):
    """Adds a new custom search category based on a base type."""
    if not base_cat_id:
        return False, "Base category ID is required."
    base_cat_id = int(base_cat_id)

    if base_cat_id not in SEARCH_CATEGORIES:
        return False, "Invalid base category type."

    base_cat_info = SEARCH_CATEGORIES[base_cat_id]

    db = DataBase("categories_search")
    all_cats = db.retrieve_all_titles() or []

    # Custom IDs: 100000 + selected category ID
    start_id = 100000 + base_cat_id

    # Find next available ID
    existing_ids = [int(c[0]) for c in all_cats if c[0].isdigit()]

    # Count total custom categories
    custom_count = 0
    for eid in existing_ids:
        if eid >= 100000:
            custom_count += 1

    if custom_count >= 10:
        return False, "Limit of 10 custom search categories reached."

    # Enforce one custom category per selected category type.
    if start_id in existing_ids:
        return (
            False,
            f"Custom category for '{base_cat_info['name']}' already exists.",
        )

    next_id = start_id

    name = f"Custom {base_cat_info['name']}"

    data = {
        "name": name,
        "base_type": base_cat_id,
        "search_sources": [],
    }

    db.store(str(next_id), json.dumps(data))
    info(
        f"Added custom search category: {name} ({next_id}) <d>-</d> <y>Please restart your *arr to apply changes.</y>"
    )
    return True, f"Custom category '{name}' added successfully."


def delete_search_category(cat_id: int):
    """Deletes a custom search category."""
    if not cat_id:
        return False, "Category ID is required."

    cat_id = int(cat_id)
    if cat_id in SEARCH_CATEGORIES:
        return False, "Cannot delete default search categories."

    db = DataBase("categories_search")
    if not db.retrieve(str(cat_id)):
        return False, f"Category {cat_id} not found."

    db.delete(str(cat_id))
    info(
        f"Deleted search category: {cat_id} <d>-</d> <y>Please restart your *arr to apply changes.</y>"
    )
    return True, f"Search category {cat_id} deleted successfully."


def search_category_exists(cat_id: int):
    """Checks if a search category exists."""
    if not cat_id:
        return False

    cat_id = int(cat_id)
    if cat_id in SEARCH_CATEGORIES:
        return True

    db = DataBase("categories_search")
    return db.retrieve(str(cat_id)) is not None


def add_download_category(name):
    """Adds a new category."""
    if not name or not name.strip():
        return False, "Category name cannot be empty."

    name = name.strip().lower()

    if len(name) > 20:
        return False, "Category name must be 20 characters or less."

    if not re.match("^[a-z0-9]+$", name):
        return (
            False,
            "Category name can only contain lowercase letters and numbers.",
        )

    db = DataBase("categories_download")
    if db.retrieve(name):
        return False, f"Category '{name}' already exists."

    all_cats = db.retrieve_all_titles() or []
    custom_count = len([c[0] for c in all_cats if c[0] not in DOWNLOAD_CATEGORIES])
    if custom_count >= 10:
        return False, "Limit of 10 custom categories reached."

    db.store(name, json.dumps({}))
    info(
        f"Added category: {name} <d>-</d> <y>Please restart your *arr to apply changes.</y>"
    )
    return True, f"Category '{name}' added successfully."


def update_download_category_mirrors(name, mirrors):
    """Updates the preferred mirrors for a category."""
    name = name.strip().lower()
    db = DataBase("categories_download")

    # Check if default
    is_default = name in DOWNLOAD_CATEGORIES

    data_str = db.retrieve(name)
    data = {}

    if data_str:
        try:
            data = json.loads(data_str)
            if isinstance(data, dict):
                data.pop("emoji", None)
        except json.JSONDecodeError:
            data = {}
    elif is_default:
        # Implicitly create default category if missing
        data = {}
    else:
        return False, f"Category '{name}' not found."

    data["mirrors"] = mirrors
    db.update_store(name, json.dumps(data))
    info(
        f"Updated mirror-whitelist for category: {name} to {mirrors} <d>-</d> <y>Please restart your *arr to apply changes.</y>"
    )
    return True, f"Category '{name}' mirror-whitelist updated successfully."


def delete_download_category(name):
    """Deletes a category."""
    name = name.strip().lower()

    if name in DOWNLOAD_CATEGORIES:
        return False, f"Cannot delete default category '{name}'."

    db = DataBase("categories_download")
    all_categories = db.retrieve_all_titles()

    if not all_categories or len(all_categories) <= 1:
        return False, "Cannot delete the last category."

    if not db.retrieve(name):
        return False, f"Category '{name}' not found."

    db.delete(name)
    info(
        f"Deleted category: {name} <d>-</d> <y>Please restart your *arr to apply changes.</y>"
    )
    return True, f"Category '{name}' deleted successfully."


def init_default_download_categories():
    """Initializes the database with default categories if they don't exist."""
    # This is now handled in get_categories to ensure they persist
    get_download_categories()


def download_category_exists(name):
    """Checks if a category exists."""
    if not name:
        return False
    db = DataBase("categories_download")
    return db.retrieve(name.lower()) is not None


def get_download_category_from_package_id(package_id):
    """Extract category from a Quasarr package ID."""
    if not package_id:
        return "not_quasarr"

    # Check all dynamic categories
    categories = get_download_categories()
    for cat in categories:
        # Check if the category name is part of the package ID
        # The package ID format is Quasarr_{category}_{hash}
        # So we look for Quasarr_{cat}_
        if f"Quasarr_{cat}_" in package_id:
            return cat
    return "not_quasarr"
