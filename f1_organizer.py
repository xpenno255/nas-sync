import os
import re
import shutil
import logging
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

import httpx

from database import (
    get_f1_config, get_f1_episodes, has_f1_season_cache,
    save_f1_episodes, create_f1_activity_log, get_f1_activity_log
)

logger = logging.getLogger(__name__)

# TheTVDB API v4
TVDB_BASE_URL = "https://api4.thetvdb.com/v4"
TVDB_F1_SERIES_ID = 387219

# Video file extensions to process
VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.ts'}

# F1 filename pattern — matches filenames containing Formula 1 / F1 indicators
F1_PATTERN = re.compile(
    r'(?:formula[.\s_-]*1|f1)[.\s_-]*'
    r'(\d{4})[.\s_-]*'               # year/season
    r'(?:r(?:ound)?[.\s_-]*(\d{1,2})[.\s_-]*)?'  # optional round number
    r'(.+?)[.\s_-]*'                  # GP name (greedy but will be trimmed)
    r'(race|qualifying|sprint[.\s_-]*(?:qualifying|shootout)?)'  # session type
    r'[.\s_-]*(.*)$',                 # remainder (quality, codec, etc.)
    re.IGNORECASE
)

# Global state
scan_in_progress = False


async def get_tvdb_token(api_key: str) -> Optional[str]:
    """Get JWT token from TheTVDB API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TVDB_BASE_URL}/login",
                json={"apikey": api_key},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("token")
            else:
                logger.error(f"TheTVDB login failed: {response.status_code}")
                return None
    except Exception as e:
        logger.error(f"TheTVDB login error: {e}")
        return None


async def fetch_tvdb_episodes(api_key: str, season: int) -> list:
    """Fetch F1 episodes for a season from TheTVDB."""
    token = await get_tvdb_token(api_key)
    if not token:
        return []

    episodes = []
    page = 0

    try:
        async with httpx.AsyncClient() as client:
            while True:
                response = await client.get(
                    f"{TVDB_BASE_URL}/series/{TVDB_F1_SERIES_ID}/episodes/official",
                    params={"season": season, "page": page},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10
                )
                if response.status_code != 200:
                    logger.error(f"TheTVDB episodes fetch failed: {response.status_code}")
                    break

                data = response.json()
                ep_list = data.get("data", {}).get("episodes", [])
                if not ep_list:
                    break

                for ep in ep_list:
                    episodes.append({
                        "episode_number": ep.get("number", 0),
                        "episode_name": ep.get("name", ""),
                        "air_date": ep.get("aired")
                    })

                # Check if there are more pages
                if not data.get("links", {}).get("next"):
                    break
                page += 1

    except Exception as e:
        logger.error(f"TheTVDB episodes fetch error: {e}")

    return episodes


async def refresh_episode_cache(api_key: str, season: int) -> bool:
    """Fetch episodes from TheTVDB and update local cache."""
    logger.info(f"Refreshing TheTVDB cache for season {season}")
    episodes = await fetch_tvdb_episodes(api_key, season)

    if not episodes:
        logger.warning(f"No episodes returned from TheTVDB for season {season}")
        return False

    await save_f1_episodes(season, episodes)
    logger.info(f"Cached {len(episodes)} episodes for season {season}")
    return True


def parse_f1_filename(filename: str) -> Optional[dict]:
    """Parse an F1 filename and extract metadata."""
    stem = Path(filename).stem
    ext = Path(filename).suffix.lower()

    if ext not in VIDEO_EXTENSIONS:
        return None

    match = F1_PATTERN.search(stem)
    if not match:
        return None

    season = int(match.group(1))
    round_num = int(match.group(2)) if match.group(2) else None
    gp_name_raw = match.group(3)
    session_raw = match.group(4)

    # Clean GP name: replace dots/underscores/dashes with spaces, strip
    gp_name = re.sub(r'[._-]+', ' ', gp_name_raw).strip()
    # Normalize to title case
    gp_name = gp_name.title()

    # Normalize session type
    session_lower = session_raw.lower().replace('.', ' ').replace('_', ' ').replace('-', ' ').strip()
    if 'sprint' in session_lower and ('qualifying' in session_lower or 'shootout' in session_lower):
        session = "Sprint Qualifying"
    elif 'sprint' in session_lower:
        session = "Sprint"
    elif 'qualifying' in session_lower:
        session = "Qualifying"
    else:
        session = "Race"

    return {
        "season": season,
        "round": round_num,
        "gp_name": gp_name,
        "session": session,
        "extension": ext
    }


def match_episode(parsed: dict, episodes: list) -> Optional[dict]:
    """Match parsed file metadata against cached TheTVDB episodes using fuzzy matching."""
    gp_name = parsed["gp_name"].lower()
    session = parsed["session"].lower()

    best_match = None
    best_score = 0.0

    for ep in episodes:
        ep_name = ep["episode_name"].lower()

        # Check if session type appears in episode name
        if session not in ep_name and session.replace(" ", "") not in ep_name.replace(" ", ""):
            continue

        # Score GP name similarity against episode name
        score = SequenceMatcher(None, gp_name, ep_name).ratio()

        # Bonus if GP name words appear in episode name
        gp_words = gp_name.split()
        matching_words = sum(1 for w in gp_words if w in ep_name)
        if gp_words:
            word_score = matching_words / len(gp_words)
            score = (score + word_score) / 2

        if score > best_score:
            best_score = score
            best_match = ep

    # Require a minimum match confidence
    if best_match and best_score >= 0.4:
        return best_match

    return None


async def scan_and_organize() -> dict:
    """Scan watch folder for F1 files, match, rename, and move them."""
    global scan_in_progress

    if scan_in_progress:
        return {"status": "skipped", "reason": "Scan already in progress"}

    config = await get_f1_config()
    if not config.get("watch_folder") or not config.get("output_folder"):
        return {"status": "error", "reason": "Watch folder or output folder not configured"}

    watch_folder = Path(config["watch_folder"])
    output_base = Path(config["output_folder"])
    api_key = config.get("tvdb_api_key", "")

    if not watch_folder.exists():
        return {"status": "error", "reason": f"Watch folder does not exist: {watch_folder}"}

    scan_in_progress = True
    results = {"status": "completed", "processed": 0, "moved": 0, "unmatched": 0, "errors": 0}
    cache_refreshed_seasons = set()

    try:
        # Find all video files in watch folder
        files = [f for f in watch_folder.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]

        for file_path in files:
            parsed = parse_f1_filename(file_path.name)
            if not parsed:
                continue  # Not an F1 file, skip silently

            results["processed"] += 1
            season = parsed["season"]

            # Ensure we have cached episodes for this season
            if not await has_f1_season_cache(season):
                if api_key and season not in cache_refreshed_seasons:
                    await refresh_episode_cache(api_key, season)
                    cache_refreshed_seasons.add(season)

            episodes = await get_f1_episodes(season)

            if not episodes:
                # No cache and no API key — can't match
                await create_f1_activity_log(
                    original_filename=file_path.name,
                    season=season,
                    status="unmatched",
                    message="No episode data available (check TheTVDB API key)"
                )
                results["unmatched"] += 1
                continue

            # Match against cached episodes
            matched = match_episode(parsed, episodes)

            if not matched:
                # Try refreshing cache once per season if we haven't already
                if api_key and season not in cache_refreshed_seasons:
                    await refresh_episode_cache(api_key, season)
                    cache_refreshed_seasons.add(season)
                    episodes = await get_f1_episodes(season)
                    matched = match_episode(parsed, episodes)

            if not matched:
                await create_f1_activity_log(
                    original_filename=file_path.name,
                    season=season,
                    status="unmatched",
                    message=f"Could not match '{parsed['gp_name']} {parsed['session']}' to any TheTVDB episode"
                )
                results["unmatched"] += 1
                continue

            # Build destination path
            ep_num = matched["episode_number"]
            ep_name = matched["episode_name"]
            new_filename = f"Formula 1 - S{season}E{ep_num:02d} - {ep_name}{parsed['extension']}"

            season_folder = output_base / "Formula 1" / f"Season {season}"
            dest_path = season_folder / new_filename

            # Check for duplicates
            if dest_path.exists():
                await create_f1_activity_log(
                    original_filename=file_path.name,
                    new_filename=new_filename,
                    season=season,
                    episode_number=ep_num,
                    status="duplicate",
                    message=f"Destination already exists: {dest_path}"
                )
                continue

            # Move file
            try:
                season_folder.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(dest_path))
                await create_f1_activity_log(
                    original_filename=file_path.name,
                    new_filename=new_filename,
                    season=season,
                    episode_number=ep_num,
                    status="moved",
                    message=f"Moved to {dest_path}"
                )
                results["moved"] += 1
                logger.info(f"F1: {file_path.name} -> {new_filename}")
            except Exception as e:
                await create_f1_activity_log(
                    original_filename=file_path.name,
                    new_filename=new_filename,
                    season=season,
                    episode_number=ep_num,
                    status="error",
                    message=str(e)
                )
                results["errors"] += 1
                logger.error(f"F1: Failed to move {file_path.name}: {e}")

    except Exception as e:
        logger.error(f"F1 scan error: {e}")
        results["status"] = "error"
        results["reason"] = str(e)
    finally:
        scan_in_progress = False

    return results


def get_f1_scan_status():
    """Get current F1 scan status."""
    return {"in_progress": scan_in_progress}
