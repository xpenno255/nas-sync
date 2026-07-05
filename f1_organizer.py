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

# Quality markers signal the end of meaningful content in F1 filenames
QUALITY_MARKERS = re.compile(
    r'[.\s_-](?:720p|1080[pi]|2160p|4K|HDTV|WEB[-.]?DL|WEB|SKY|F1TV)',
    re.IGNORECASE
)

# GP name adjective/keyword to TheTVDB location mapping
GP_NAME_MAP = {
    "australian": "australia",
    "chinese": "china",
    "japanese": "japan",
    "miami": "miami",
    "canadian": "canada",
    "monaco": "monaco",
    "spanish": "spain",
    "austrian": "austria",
    "british": "great britain",
    "belgian": "belgium",
    "hungarian": "hungary",
    "dutch": "netherlands",
    "italian": "italy",
    "azerbaijan": "azerbaijan",
    "singapore": "singapore",
    "american": "united states",
    "united states": "united states",
    "mexican": "mexico",
    "brazilian": "brazil",
    "las vegas": "las vegas",
    "qatar": "qatar",
    "abu dhabi": "abu dhabi",
    "bahrain": "bahrain",
    "saudi arabian": "saudi arabia",
    "emilia romagna": "emilia romagna",
    "barcelona": "barcelona-catalunya",
    "catalan": "barcelona-catalunya",
    "portugal": "portugal",
    "portuguese": "portugal",
}

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

    # Check if this is an F1 file (allow optional leading numeric prefix e.g. "05.")
    f1_prefix = re.match(
        r'(?:\d+[.\s_-]+)?(?:formula[.\s_-]*1|f1)[.\s_-]*',
        stem, re.IGNORECASE
    )
    if not f1_prefix:
        return None

    remainder = stem[f1_prefix.end():]

    # Try to extract season/episode info
    season = None
    round_num = None
    episode_num = None

    # Format: S2025E105
    sxe_match = re.match(r'S(\d{4})E(\d+)[.\s_-]*(.*)', remainder, re.IGNORECASE)
    if sxe_match:
        season = int(sxe_match.group(1))
        episode_num = int(sxe_match.group(2))
        remainder = sxe_match.group(3)
    else:
        # Format: 2025.Round20.xxx, 2026x09.xxx (year-x-round), or just 2025.xxx
        year_match = re.match(r'(\d{4})(?:[xX](\d{1,2}))?[.\s_-]*(.*)', remainder)
        if year_match:
            season = int(year_match.group(1))
            if year_match.group(2):
                round_num = int(year_match.group(2))
            remainder = year_match.group(3)

            # Check for Round number
            round_match = re.match(r'[Rr](?:ound)?[.\s_-]*(\d{1,2})[.\s_-]*(.*)', remainder)
            if round_match:
                round_num = int(round_match.group(1))
                remainder = round_match.group(2)

    if not season:
        return None

    # Split off quality/technical info
    quality_split = QUALITY_MARKERS.search(remainder)
    if quality_split:
        content = remainder[:quality_split.start()]
    else:
        content = remainder

    # Clean content: replace dots/underscores/dashes with spaces
    content = re.sub(r'[._-]+', ' ', content).strip()

    if not content:
        return None

    # Extract GP name and session type from content
    # Known session keywords (order matters - check longer phrases first)
    SESSION_PATTERNS = [
        (r'\bTeds\s+Sprint\s+Race\s+Notebook\b', 'Teds Sprint Race Notebook'),
        (r'\bTeds\s+Qualifying\s+Notebook\b', 'Teds Qualifying Notebook'),
        (r'\bTeds\s+Notebook\b', 'Teds Notebook'),
        (r'\bTeam\s+Principals?\s+Press\s+Conference\b', 'Team Principals Press Conference'),
        (r'\bDrivers?\s+Press\s+Conference\b', 'Drivers Press Conference'),
        (r'\bPress\s+Conference\b', 'Press Conference'),
        (r'\bSprint\s+(?:Qualifying|Shootout)\b', 'Sprint Qualifying'),
        (r'\bSprint\s+Race\b', 'Sprint Race'),
        (r'\bSprint\b', 'Sprint'),
        (r'\bQualifying\b', 'Qualifying'),
        (r'\bPractice\s+(?:One|1)\b', 'Practice 1'),
        (r'\bPractice\s+(?:Two|2)\b', 'Practice 2'),
        (r'\bPractice\s+(?:Three|3)\b', 'Practice 3'),
        (r'\bFree\s+Practice\s+(?:One|1)\b', 'Practice 1'),
        (r'\bFree\s+Practice\s+(?:Two|2)\b', 'Practice 2'),
        (r'\bFree\s+Practice\s+(?:Three|3)\b', 'Practice 3'),
        (r'\bFP1\b', 'Practice 1'),
        (r'\bFP2\b', 'Practice 2'),
        (r'\bFP3\b', 'Practice 3'),
        (r'\bFree\s+Practice\b', 'Free Practice'),
        (r'\bWeekend\s+Warm[\s-]+Up\b', 'Weekend Warm-Up'),
        (r'\bRace\b', 'Race'),
        (r'\bPaddock\s+Uncut\b', 'Paddock Uncut'),
        (r'\bThe\s+F1\s+Show\b', 'The F1 Show'),
        (r'\bUNCUT\b', 'Race'),
    ]

    session = None
    gp_name = content

    for pattern, session_name in SESSION_PATTERNS:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            session = session_name
            # GP name is everything before the session match
            gp_name = content[:match.start()].strip()
            break

    # If no session found, default to Race
    if not session:
        session = 'Race'

    # If GP name is empty, return None
    if not gp_name:
        return None

    # Normalize GP name to title case
    gp_name = gp_name.title()

    return {
        "season": season,
        "round": round_num,
        "episode_num": episode_num,
        "gp_name": gp_name,
        "session": session,
        "extension": ext
    }


def _normalize_gp_to_location(gp_name: str) -> str:
    """Convert a GP name like 'Japanese Grand Prix' to a TheTVDB location like 'japan'."""
    # Remove common suffixes
    clean = re.sub(r'\s*(grand\s+prix|gp)\s*$', '', gp_name, flags=re.IGNORECASE).strip().lower()

    # Look up in mapping
    if clean in GP_NAME_MAP:
        return GP_NAME_MAP[clean]

    # Try each word individually (handles cases like "Mexico City Grand Prix" -> "mexico")
    for word in clean.split():
        if word in GP_NAME_MAP:
            return GP_NAME_MAP[word]

    # Fallback: return cleaned name as-is (might still match via fuzzy)
    return clean


def _parse_tvdb_episode(episode_name: str) -> tuple:
    """Parse TheTVDB episode name like 'Japan (Race)' into (location, session)."""
    match = re.match(r'^(.+?)\s*\((.+?)\)\s*$', episode_name)
    if match:
        return match.group(1).strip().lower(), match.group(2).strip().lower()
    return episode_name.strip().lower(), None


def _normalize_session_for_tvdb(session: str) -> str:
    """Normalize our parsed session to match TheTVDB session format."""
    s = session.lower()
    if s == "sprint race":
        return "sprint race"
    if s == "sprint qualifying":
        return "sprint qualifying"
    if s == "sprint":
        return "sprint race"  # "Sprint" in filename usually means Sprint Race
    return s


def match_episode(parsed: dict, episodes: list) -> Optional[dict]:
    """Match parsed file metadata against cached TheTVDB episodes."""
    # Direct match by episode number (SxxExx format)
    if parsed.get("episode_num"):
        for ep in episodes:
            if ep["episode_number"] == parsed["episode_num"]:
                return ep

    location = _normalize_gp_to_location(parsed["gp_name"])
    session = _normalize_session_for_tvdb(parsed["session"])

    # First pass: exact location and session match
    for ep in episodes:
        tvdb_location, tvdb_session = _parse_tvdb_episode(ep["episode_name"])
        if tvdb_location == location and tvdb_session == session:
            return ep

    # Second pass: fuzzy location match with exact session
    best_match = None
    best_score = 0.0
    for ep in episodes:
        tvdb_location, tvdb_session = _parse_tvdb_episode(ep["episode_name"])
        if tvdb_session != session and not (tvdb_session and tvdb_session.startswith(session)):
            continue

        score = SequenceMatcher(None, location, tvdb_location).ratio()
        # Boost score if one location string contains the other
        if location in tvdb_location or tvdb_location in location:
            score = max(score, 0.8)

        if score > best_score:
            best_score = score
            best_match = ep

    if best_match and best_score >= 0.6:
        return best_match

    return None


def _is_sample(file_path: Path, watch_folder: Path) -> bool:
    """Preview clip: 'sample' in the filename or in any parent folder name."""
    if 'sample' in file_path.name.lower():
        return True
    try:
        rel_parts = file_path.relative_to(watch_folder).parts[:-1]
    except ValueError:
        rel_parts = file_path.parts[:-1]
    return any('sample' in part.lower() for part in rel_parts)


def _job_folder_for(file_path: Path, watch_folder: Path) -> Optional[Path]:
    """The top-level job folder under the watch folder that contains this file."""
    try:
        rel = file_path.relative_to(watch_folder)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None  # loose file directly in the watch folder
    return watch_folder / rel.parts[0]


async def _move_file(file_path: Path, dest_folder: Path, new_filename: str,
                     season: int, episode_number: Optional[int], status: str,
                     message: str, output_base: Path, results: dict) -> bool:
    """Move a file into the F1 library, handling duplicate destinations."""
    dest_path = dest_folder / new_filename

    # If the destination exists, park the source in _duplicates so it isn't
    # rescanned and re-logged forever.
    if dest_path.exists():
        duplicates_folder = output_base / "F1" / "_duplicates"
        duplicate_dest = duplicates_folder / file_path.name
        try:
            duplicates_folder.mkdir(parents=True, exist_ok=True)
            if duplicate_dest.exists():
                duplicate_dest = duplicates_folder / f"{file_path.stem}.{int(datetime.now().timestamp())}{file_path.suffix}"
            shutil.move(str(file_path), str(duplicate_dest))
            dup_message = f"Destination already exists ({dest_path}) — moved to {duplicate_dest}"
        except OSError as e:
            dup_message = f"Destination already exists ({dest_path}) — failed to move source aside: {e}"

        await create_f1_activity_log(
            original_filename=file_path.name, new_filename=new_filename,
            season=season, episode_number=episode_number,
            status="duplicate", message=dup_message
        )
        return True

    try:
        dest_folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file_path), str(dest_path))
        await create_f1_activity_log(
            original_filename=file_path.name, new_filename=new_filename,
            season=season, episode_number=episode_number,
            status=status, message=message
        )
        logger.info(f"F1: {file_path.name} -> {new_filename}")
        return True
    except Exception as e:
        await create_f1_activity_log(
            original_filename=file_path.name, new_filename=new_filename,
            season=season, episode_number=episode_number,
            status="error", message=str(e)
        )
        results["errors"] += 1
        logger.error(f"F1: Failed to move {file_path.name}: {e}")
        return False


async def _cleanup_job_folders(job_folders: set, watch_folder: Path):
    """Remove job folders whose media has all been moved out (only junk remains)."""
    for folder in job_folders:
        try:
            if not folder.is_dir() or folder.resolve() == watch_folder.resolve():
                continue
            remaining_media = [
                f for f in folder.rglob('*')
                if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
                and 'sample' not in f.name.lower()
            ]
            if remaining_media:
                continue
            shutil.rmtree(folder)
            await create_f1_activity_log(
                original_filename=folder.name,
                status="cleaned",
                message="Removed job folder after moving its media"
            )
            logger.info(f"F1: removed emptied job folder {folder.name}")
        except OSError as e:
            logger.warning(f"F1: could not remove job folder {folder}: {e}")


async def scan_and_organize() -> dict:
    """Scan watch folder for F1 files, match, rename, and move them.

    Parsed F1 files are ALWAYS moved into the F1 library: matched files get the
    TheTVDB episode name, unmatched ones get a best-effort "F1 - S<year> - <GP>
    (<Session>)" fallback name so nothing is left behind in the downloads folder.
    """
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
    results = {"status": "completed", "processed": 0, "moved": 0, "moved_unmatched": 0, "errors": 0}

    try:
        # Find all video files in watch folder. Excludes output_base in case it's
        # nested inside watch_folder, and sample clips (which previously could get
        # matched and moved as the real episode).
        files = [
            f for f in watch_folder.rglob('*')
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
            and output_base.resolve() not in f.resolve().parents
            and not _is_sample(f, watch_folder)
        ]

        touched_job_folders = set()

        async def organize_one(file_path, parsed, segment=None):
            """Match and move a single file; returns True if it left the watch folder."""
            results["processed"] += 1
            season = parsed["season"]
            season_folder = output_base / "F1" / f"Season {season}"
            suffix = f" - {segment}" if segment else ""

            episodes = await get_f1_episodes(season)
            matched = match_episode(parsed, episodes) if episodes else None

            if matched:
                ep_num = matched["episode_number"]
                new_filename = f"F1 - S{season}E{ep_num:02d} - {matched['episode_name']}{suffix}{parsed['extension']}"
                moved = await _move_file(
                    file_path, season_folder, new_filename, season, ep_num,
                    "moved", f"Moved to {season_folder / new_filename}",
                    output_base, results
                )
                if moved:
                    results["moved"] += 1
            else:
                # No cache or no episode match — move it anyway with a best-effort
                # name so it never lingers in the downloads folder.
                reason = (
                    f"no episode cache for season {season}" if not episodes
                    else f"no TheTVDB match for '{parsed['gp_name']} {parsed['session']}'"
                )
                new_filename = f"F1 - S{season} - {parsed['gp_name']} ({parsed['session']}){suffix}{parsed['extension']}"
                moved = await _move_file(
                    file_path, season_folder, new_filename, season, None,
                    "moved_unmatched",
                    f"Moved with fallback name ({reason}) to {season_folder / new_filename}",
                    output_base, results
                )
                if moved:
                    results["moved_unmatched"] += 1

            if moved:
                job = _job_folder_for(file_path, watch_folder)
                if job:
                    touched_job_folders.add(job)
            return moved

        # First pass: files whose own name parses. Collect the rest per job folder.
        unparsed_by_job = {}
        for file_path in files:
            parsed = parse_f1_filename(file_path.name)
            if parsed:
                await organize_one(file_path, parsed)
                continue
            job_folder = _job_folder_for(file_path, watch_folder)
            if job_folder:
                unparsed_by_job.setdefault(job_folder, []).append(file_path)

        # Second pass: Sky-style multi-part releases unpack to segment files with
        # generic names (01.Pre-Race.Buildup.mp4, 02.Race.Session.mp4, ...) — the
        # release name lives on the job folder. Parse the folder name instead: the
        # largest file (the session itself) gets the canonical episode name, the
        # extras keep a label from their own filename.
        for job_folder, job_files in unparsed_by_job.items():
            folder_parsed = parse_f1_filename(job_folder.name + job_files[0].suffix.lower())
            if not folder_parsed:
                continue  # Not an F1 job folder, skip silently

            job_files.sort(key=lambda f: f.stat().st_size, reverse=True)
            for idx, file_path in enumerate(job_files):
                parsed = dict(folder_parsed)
                parsed["extension"] = file_path.suffix.lower()
                segment = re.sub(r'[._-]+', ' ', file_path.stem).strip() if idx > 0 else None
                await organize_one(file_path, parsed, segment=segment)

        await _cleanup_job_folders(touched_job_folders, watch_folder)

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
