"""Rescue obfuscated/hidden video files from the nzbget completed downloads folder.

Some releases unpack to hidden dot-files or gibberish filenames that Sonarr/Radarr
can't parse, so they never get imported and sit on disk forever. This module scans
each completed job folder, renames such video files to the job folder's name (which
is the release name the *arr stack can parse), and optionally removes leftover junk.
"""

import re
import time
import logging
from pathlib import Path

from database import get_cleanup_config, create_cleanup_activity_log

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.ts', '.m4v', '.wmv', '.mpg', '.mpeg'}

# Non-video leftovers safe to remove when the user enables junk removal
JUNK_EXTENSIONS = {'.par2', '.sfv', '.srr', '.nzb', '.url', '.lnk'}

# Gibberish detection: long run of hex, or a long single token with no separators
HEX_NAME = re.compile(r'^[a-fA-F0-9]{16,}$')
OBFUSCATED_NAME = re.compile(r'^[a-zA-Z0-9+]{20,}$')

# Global scan state
cleanup_in_progress = False


def is_obfuscated_video(path: Path) -> bool:
    """A video file the *arr stack likely can't parse: hidden, or a gibberish name."""
    if path.name.startswith('.'):
        return True
    stem = path.stem
    if HEX_NAME.match(stem):
        return True
    # Single long token with no dots/spaces/dashes — real releases have separators
    if OBFUSCATED_NAME.match(stem) and not re.search(r'[.\s_-]', stem):
        return True
    return False


def is_junk_file(path: Path) -> bool:
    if path.suffix.lower() in JUNK_EXTENSIONS:
        return True
    # Hidden non-video files (e.g. leftover .hidden par sets)
    if path.name.startswith('.') and path.suffix.lower() not in VIDEO_EXTENSIONS:
        return True
    return False


def _folder_is_settling(folder: Path, min_age_minutes: int) -> bool:
    """True if anything in the folder was modified too recently to touch safely."""
    cutoff = time.time() - min_age_minutes * 60
    try:
        for f in folder.rglob('*'):
            if f.is_file() and f.stat().st_mtime > cutoff:
                return True
    except OSError:
        return True
    return False


async def _process_job_folder(job_folder: Path, remove_junk: bool, dry_run: bool,
                              results: dict):
    """Rename obfuscated videos to the job folder name; optionally remove junk."""
    files = [f for f in job_folder.rglob('*') if f.is_file()]

    videos = sorted(
        [f for f in files if f.suffix.lower() in VIDEO_EXTENSIONS and is_obfuscated_video(f)],
        key=lambda f: f.stat().st_size,
        reverse=True
    )

    for idx, video in enumerate(videos):
        # Largest file gets the clean release name; extras get a numeric suffix
        suffix = f" - {idx + 1}" if idx > 0 else ""
        new_name = f"{job_folder.name}{suffix}{video.suffix.lower()}"
        dest = job_folder / new_name

        if dest.exists():
            results["skipped"] += 1
            results["actions"].append({
                "job_folder": job_folder.name, "original": video.name,
                "new": new_name, "action": "skipped",
                "message": "Destination filename already exists"
            })
            await create_cleanup_activity_log(
                job_folder.name, video.name, new_name, "skipped",
                "Destination filename already exists", dry_run
            )
            continue

        message = f"Renamed to {new_name}"
        try:
            if not dry_run:
                video.rename(dest)
            results["renamed"] += 1
            results["actions"].append({
                "job_folder": job_folder.name, "original": video.name,
                "new": new_name, "action": "renamed", "message": message
            })
            await create_cleanup_activity_log(
                job_folder.name, video.name, new_name, "renamed", message, dry_run
            )
            if not dry_run:
                logger.info(f"Cleanup: {job_folder.name}/{video.name} -> {new_name}")
        except OSError as e:
            results["errors"] += 1
            results["actions"].append({
                "job_folder": job_folder.name, "original": video.name,
                "new": new_name, "action": "error", "message": str(e)
            })
            await create_cleanup_activity_log(
                job_folder.name, video.name, new_name, "error", str(e), dry_run
            )

    if remove_junk:
        for f in files:
            if not f.exists() or not is_junk_file(f):
                continue
            try:
                if not dry_run:
                    f.unlink()
                results["junk_removed"] += 1
                results["actions"].append({
                    "job_folder": job_folder.name, "original": f.name,
                    "new": None, "action": "junk_removed", "message": "Removed junk file"
                })
                await create_cleanup_activity_log(
                    job_folder.name, f.name, None, "junk_removed", "Removed junk file", dry_run
                )
            except OSError as e:
                results["errors"] += 1
                await create_cleanup_activity_log(
                    job_folder.name, f.name, None, "error", str(e), dry_run
                )


async def run_cleanup(dry_run: bool = False) -> dict:
    """Scan the completed downloads folder and rescue unimported files."""
    global cleanup_in_progress

    if cleanup_in_progress:
        return {"status": "skipped", "reason": "Cleanup already in progress", "dry_run": dry_run}

    config = await get_cleanup_config()
    if not config.get("watch_folder"):
        return {"status": "error", "reason": "Completed downloads folder not configured", "dry_run": dry_run}

    watch_folder = Path(config["watch_folder"])
    if not watch_folder.is_dir():
        return {"status": "error", "reason": f"Folder does not exist: {watch_folder}", "dry_run": dry_run}

    min_age = int(config.get("min_age_minutes") or 60)
    remove_junk = bool(config.get("remove_junk"))

    cleanup_in_progress = True
    results = {
        "status": "completed", "dry_run": dry_run, "scanned": 0,
        "renamed": 0, "junk_removed": 0, "skipped": 0, "errors": 0, "actions": []
    }

    try:
        for entry in sorted(watch_folder.iterdir()):
            # Skip nzbget working dirs (_unpack, _failed) and hidden dirs
            if not entry.is_dir() or entry.name.startswith(('_', '.')):
                continue

            results["scanned"] += 1

            if _folder_is_settling(entry, min_age):
                logger.info(f"Cleanup: skipping '{entry.name}' — modified within the last {min_age} minutes")
                continue

            await _process_job_folder(entry, remove_junk, dry_run, results)

    except Exception as e:
        logger.error(f"Cleanup scan error: {e}")
        results["status"] = "error"
        results["reason"] = str(e)
    finally:
        cleanup_in_progress = False

    return results


def get_cleanup_status():
    return {"in_progress": cleanup_in_progress}
