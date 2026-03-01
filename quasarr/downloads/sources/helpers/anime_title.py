# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from dataclasses import dataclass
from typing import List, Optional

from quasarr.constants import LANGUAGE_TO_ALPHA2, SUBTITLE_TOKEN_BY_ALPHA2
from quasarr.providers.utils import sanitize_title


@dataclass
class ReleaseInfo:
    release_title: Optional[str]
    audio_langs: List[str]
    subtitle_langs: List[str]
    episode_title: Optional[str]
    resolution: str
    audio: str
    video: str
    source: str
    release_group: str
    season_part: Optional[int]
    season: Optional[int]
    episode_min: Optional[int]
    episode_max: Optional[int]


def subtitle_lang_to_alpha2(lang: str) -> Optional[str]:
    if not lang:
        return None

    normalized = re.sub(r"[^a-z]", "", lang.lower())
    if not normalized:
        return None

    mapped = LANGUAGE_TO_ALPHA2.get(normalized)
    if mapped:
        return mapped

    if len(normalized) == 2:
        return normalized.upper()

    return None


def subtitle_tokens(subtitle_langs: List[str]) -> List[str]:
    tokens: List[str] = []
    for lang in subtitle_langs:
        code = subtitle_lang_to_alpha2(lang)
        token = SUBTITLE_TOKEN_BY_ALPHA2.get(code) if code else None
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def inject_subtitle_tokens_in_title(title: str, subtitle_langs: List[str]) -> str:
    """
    Canonical subtitle marker format:
    - `GerSub`, `EngSub`, `JapSub`
    """
    has_group = re.search(r"-[^.\s]+$", title)
    title_without_group = title
    group_suffix = ""
    if has_group:
        title_without_group, _, group = title.rpartition("-")
        group_suffix = f"-{group}"

    tokens = [t for t in title_without_group.split(".") if t]

    subtitle_token_by_lower = {
        token.lower(): token for token in SUBTITLE_TOKEN_BY_ALPHA2.values()
    }
    existing_subtitle_tokens: List[str] = []
    first_existing_marker_idx = None

    def add_unique_subtitle_token(token: str, target: List[str]) -> None:
        canonical = subtitle_token_by_lower.get(token.lower())
        if canonical and canonical not in target:
            target.append(canonical)

    cleaned_tokens: List[str] = []
    for token in tokens:
        if token.lower() in subtitle_token_by_lower:
            if first_existing_marker_idx is None:
                first_existing_marker_idx = len(cleaned_tokens)
            add_unique_subtitle_token(token, existing_subtitle_tokens)
            continue
        cleaned_tokens.append(token)
    tokens = cleaned_tokens

    first_subbed_idx = None
    cleaned_tokens = []
    for token in tokens:
        if token.lower() == "subbed":
            if first_subbed_idx is None:
                first_subbed_idx = len(cleaned_tokens)
            continue
        cleaned_tokens.append(token)
    tokens = cleaned_tokens

    resolved_subtitle_tokens = subtitle_tokens(subtitle_langs)
    if not resolved_subtitle_tokens:
        resolved_subtitle_tokens = existing_subtitle_tokens

    if resolved_subtitle_tokens:
        if first_subbed_idx is not None:
            insert_at = first_subbed_idx
        elif first_existing_marker_idx is not None:
            insert_at = first_existing_marker_idx
        else:
            insert_at = len(tokens)
        insert_at = max(0, min(insert_at, len(tokens)))
        tokens[insert_at:insert_at] = resolved_subtitle_tokens

    normalized_title = ".".join(tokens) + group_suffix
    return sanitize_title(normalized_title)


def guess_release_title(page_title: str, release_info: ReleaseInfo) -> str:
    clean_title = _clean_series_title(page_title)

    if release_info.season is not None:
        season_token = f"S{release_info.season:02d}"
    else:
        season_token = ""

    episode_token = ""
    if release_info.episode_min is not None:
        episode_min = release_info.episode_min
        episode_max = (
            release_info.episode_max
            if release_info.episode_max is not None
            else release_info.episode_min
        )
        episode_token = f"E{episode_min:02d}"
        if episode_max != episode_min:
            episode_token += f"-{episode_max:02d}"

    title_core = clean_title.strip().replace(" ", ".")
    if season_token:
        title_core += f".{season_token}{episode_token}"
    elif episode_token:
        title_core += f".{episode_token}"

    episode_title = _clean_episode_title(release_info.episode_title)
    if episode_title:
        title_core += f".{episode_title.replace(' ', '.')}"

    parts = [title_core]

    if release_info.season_part:
        part_string = f"Part.{release_info.season_part}"
        if part_string not in title_core:
            parts.append(part_string)

    audio_prefix = _derive_audio_prefix(release_info.audio_langs)
    if audio_prefix:
        parts.append(audio_prefix)

    resolved_subtitle_tokens = subtitle_tokens(release_info.subtitle_langs)
    if resolved_subtitle_tokens:
        parts.extend(resolved_subtitle_tokens)

    if release_info.audio:
        parts.append(release_info.audio)

    if release_info.resolution:
        parts.append(release_info.resolution)

    if release_info.source:
        parts.append(release_info.source)

    if release_info.video:
        parts.append(release_info.video)

    title = ".".join(parts)
    if release_info.release_group:
        title += f"-{release_info.release_group}"

    return inject_subtitle_tokens_in_title(title, release_info.subtitle_langs)


def _derive_audio_prefix(audio_langs: List[str]) -> str:
    if len(audio_langs) > 2 and "German" in audio_langs:
        return "German.ML"
    if len(audio_langs) == 2 and "German" in audio_langs:
        return "German.DL"
    if len(audio_langs) == 1 and "German" in audio_langs:
        return "German"
    if audio_langs:
        return audio_langs[0]
    return ""


def _clean_series_title(page_title: str) -> str:
    clean_title = str(page_title or "").strip()

    clean_title = re.sub(r"^\[[^\]]+\]\s*", "", clean_title)
    clean_title = re.sub(r"\s*\(\d{4}\)\s*$", "", clean_title)
    clean_title = re.sub(r"\s*\([^)]*\)\s*$", "", clean_title)
    clean_title = re.sub(
        r"(?i)\b(?:Season|Staffel)\s*\.?\s*\d+\b|\bR\d+\b", "", clean_title
    )
    clean_title = re.sub(r"\s*[-:|]\s*$", "", clean_title).strip()
    clean_title = re.sub(r"\s{2,}", " ", clean_title)

    return clean_title


def _clean_episode_title(episode_title: Optional[str]) -> str:
    cleaned = str(episode_title or "").strip()
    cleaned = re.sub(r"^[\s\-:|/._]+", "", cleaned)
    cleaned = re.sub(r"[\s\-:|/._]+$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned
