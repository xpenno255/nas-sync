"""Rescue nzbget completed downloads that Sonarr/Radarr/Lidarr never imported.

nzbget's completed folder is organized as completed/<category>/<job-folder>/...
(category is e.g. Movies, TV, Music — matching the *arr instance that owns it).
Two distinct failure modes leave a job folder stuck there forever, slowly filling
the disk:

  1. The release's media file has a hidden or gibberish name the *arr stack can't
     parse — we rename it to the job folder's name (the actual release name).
  2. nzbget's own unpacker never extracted a multi-part RAR set, so there's no
     media file at all yet — we extract it with `unar`, then apply the same
     rename check to whatever comes out.

Either way we fix the file in place and leave it for the *arr instance's own
already-configured import scan to pick up — we don't move anything into the
library ourselves, since only the *arr apps have the metadata to do that properly.
"""

import re
import time
import shutil
import asyncio
import logging
from pathlib import Path

from database import get_cleanup_config, create_cleanup_activity_log

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.ts', '.m4v', '.wmv', '.mpg', '.mpeg'}
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.wma', '.opus'}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Non-media leftovers safe to remove when the user enables junk removal.
# RAR parts are handled separately by the extraction step, not generic junk removal.
JUNK_EXTENSIONS = {'.par2', '.sfv', '.srr', '.nzb', '.url', '.lnk'}

# Multi-part RAR volumes: old-style (name.rar + name.r00, name.r01, ...) and
# new-style (name.part01.rar, name.part02.rar, ...)
RAR_VOLUME_PATTERN = re.compile(r'\.(rar|r\d{2,3})$', re.IGNORECASE)
RAR_PART_N_PATTERN = re.compile(r'\.part0*(\d+)\.rar$', re.IGNORECASE)

# Gibberish detection: long run of hex, or a long single token with no separators
HEX_NAME = re.compile(r'^[a-fA-F0-9]{16,}$')
OBFUSCATED_NAME = re.compile(r'^[a-zA-Z0-9+]{20,}$')

EXTRACT_TIMEOUT_SECONDS = 1800

# Global scan state
cleanup_in_progress = False


def _in_sample_dir(path: Path, root: Path) -> bool:
    """True if path lives inside a subfolder named 'sample' under root (preview clip)."""
    return any(part.lower() == 'sample' for part in path.relative_to(root).parts[:-1])


def is_obfuscated_media(path: Path) -> bool:
    """A media file the *arr stack likely can't parse: hidden, or a gibberish name."""
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
    # Hidden non-media files (e.g. leftover .hidden par sets)
    if path.name.startswith('.') and path.suffix.lower() not in MEDIA_EXTENSIONS:
        return True
    return False


def _pick_rar_entrypoint(rar_parts: list) -> Path:
    """Pick the volume to hand to unar: the plain .rar, or the lowest part number."""
    for f in rar_parts:
        if f.suffix.lower() == '.rar' and not RAR_PART_N_PATTERN.search(f.name):
            return f
    numbered = [(RAR_PART_N_PATTERN.search(f.name), f) for f in rar_parts]
    numbered = [(int(m.group(1)), f) for m, f in numbered if m]
    if numbered:
        return min(numbered, key=lambda t: t[0])[1]
    return sorted(rar_parts, key=lambda f: f.name)[0]


async def _extract_rar(rar_path: Path, dest_dir: Path) -> tuple:
    """Extract a (possibly multi-part) RAR archive in place using unar."""
    try:
        process = await asyncio.create_subprocess_exec(
            "unar", "-D", "-f", "-q", "-o", str(dest_dir), str(rar_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=EXTRACT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return False, "Extraction timed out"

        if process.returncode == 0:
            return True, ""
        return False, (stderr.decode(errors="replace").strip() or f"unar exited with code {process.returncode}")
    except FileNotFoundError:
        return False, "unar is not installed in this container"
    except OSError as e:
        return False, str(e)


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


async def _rename_obfuscated(job_folder: Path, media_files: list, dry_run: bool, results: dict):
    """Rename obfuscated media files to the job folder's (parseable) name."""
    obfuscated = sorted(
        [f for f in media_files if is_obfuscated_media(f)],
        key=lambda f: f.stat().st_size,
        reverse=True
    )

    for idx, media in enumerate(obfuscated):
        suffix = f" - {idx + 1}" if idx > 0 else ""
        new_name = f"{job_folder.name}{suffix}{media.suffix.lower()}"
        dest = job_folder / new_name

        if dest.exists():
            results["skipped"] += 1
            await create_cleanup_activity_log(
                job_folder.name, media.name, new_name, "skipped",
                "Destination filename already exists", dry_run
            )
            results["actions"].append({
                "job_folder": job_folder.name, "original": media.name,
                "new": new_name, "action": "skipped",
                "message": "Destination filename already exists"
            })
            continue

        message = f"Renamed to {new_name}"
        try:
            if not dry_run:
                media.rename(dest)
            results["renamed"] += 1
            await create_cleanup_activity_log(
                job_folder.name, media.name, new_name, "renamed", message, dry_run
            )
            results["actions"].append({
                "job_folder": job_folder.name, "original": media.name,
                "new": new_name, "action": "renamed", "message": message
            })
            if not dry_run:
                logger.info(f"Cleanup: {job_folder.name}/{media.name} -> {new_name}")
        except OSError as e:
            results["errors"] += 1
            await create_cleanup_activity_log(
                job_folder.name, media.name, new_name, "error", str(e), dry_run
            )
            results["actions"].append({
                "job_folder": job_folder.name, "original": media.name,
                "new": new_name, "action": "error", "message": str(e)
            })


async def _extract_stuck_archive(job_folder: Path, rar_parts: list, dry_run: bool, results: dict):
    """Extract an un-unpacked RAR set, then rename the result if it's obfuscated."""
    entrypoint = _pick_rar_entrypoint(rar_parts)
    total_size = sum(f.stat().st_size for f in rar_parts)

    if dry_run:
        results["extracted"] += 1
        message = f"Would extract {len(rar_parts)}-part RAR set (~{total_size / (1<<20):.0f} MB) via {entrypoint.name}"
        await create_cleanup_activity_log(
            job_folder.name, entrypoint.name, None, "extracted", message, dry_run
        )
        results["actions"].append({
            "job_folder": job_folder.name, "original": entrypoint.name,
            "new": None, "action": "extracted", "message": message
        })
        return

    # Extraction briefly needs room for the new media file alongside the still-present
    # RAR parts, so require some headroom rather than let unar fail with an opaque
    # "opening file failed" when the disk is nearly full.
    try:
        free_space = shutil.disk_usage(job_folder).free
    except OSError:
        free_space = None
    if free_space is not None and free_space < total_size * 1.1:
        results["errors"] += 1
        message = (
            f"Skipped: only {free_space / (1<<30):.1f} GB free, "
            f"need ~{total_size * 1.1 / (1<<30):.1f} GB to safely extract"
        )
        await create_cleanup_activity_log(
            job_folder.name, entrypoint.name, None, "error", message, dry_run
        )
        results["actions"].append({
            "job_folder": job_folder.name, "original": entrypoint.name,
            "new": None, "action": "error", "message": message
        })
        return

    ok, error = await _extract_rar(entrypoint, job_folder)
    if not ok:
        results["errors"] += 1
        message = f"Extraction failed: {error}"
        await create_cleanup_activity_log(
            job_folder.name, entrypoint.name, None, "error", message, dry_run
        )
        results["actions"].append({
            "job_folder": job_folder.name, "original": entrypoint.name,
            "new": None, "action": "error", "message": message
        })
        return

    extracted_media = [
        f for f in job_folder.rglob('*')
        if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS and not _in_sample_dir(f, job_folder)
    ]

    if not extracted_media:
        results["errors"] += 1
        message = "Extraction completed but no media file was found in the result"
        await create_cleanup_activity_log(
            job_folder.name, entrypoint.name, None, "error", message, dry_run
        )
        results["actions"].append({
            "job_folder": job_folder.name, "original": entrypoint.name,
            "new": None, "action": "error", "message": message
        })
        return

    # Extraction succeeded — the release name is usually already intact inside the
    # RAR, so just clean up the now-redundant archive parts and fix the name only
    # if it's still obfuscated somehow.
    for part in rar_parts:
        try:
            part.unlink()
        except OSError:
            pass

    message = f"Extracted {len(rar_parts)}-part RAR set"
    results["extracted"] += 1
    await create_cleanup_activity_log(
        job_folder.name, entrypoint.name, extracted_media[0].name, "extracted", message, dry_run
    )
    results["actions"].append({
        "job_folder": job_folder.name, "original": entrypoint.name,
        "new": extracted_media[0].name, "action": "extracted", "message": message
    })
    logger.info(f"Cleanup: extracted {job_folder.name} ({len(rar_parts)} parts)")

    await _rename_obfuscated(job_folder, extracted_media, dry_run, results)


async def _cleanup_redundant_rar_parts(job_folder: Path, rar_parts: list, dry_run: bool, results: dict):
    """Remove RAR parts left behind once a valid media file already exists —
    e.g. after a prior manual extraction, or a partial success some other way.
    Always removed regardless of the remove_junk toggle: once real media exists,
    these are pure disk waste, not the "leave it alone" kind of junk."""
    for part in rar_parts:
        try:
            if not dry_run:
                part.unlink()
            results["junk_removed"] += 1
            await create_cleanup_activity_log(
                job_folder.name, part.name, None, "junk_removed",
                "Removed redundant RAR part (media already present)", dry_run
            )
            results["actions"].append({
                "job_folder": job_folder.name, "original": part.name,
                "new": None, "action": "junk_removed",
                "message": "Removed redundant RAR part (media already present)"
            })
        except OSError as e:
            results["errors"] += 1
            await create_cleanup_activity_log(
                job_folder.name, part.name, None, "error", str(e), dry_run
            )


async def _process_job_folder(job_folder: Path, remove_junk: bool, dry_run: bool, results: dict):
    """Fix one stuck job folder: rename obfuscated media, or extract a stuck RAR set."""
    files = [f for f in job_folder.rglob('*') if f.is_file()]

    media_files = [
        f for f in files
        if f.suffix.lower() in MEDIA_EXTENSIONS and not _in_sample_dir(f, job_folder)
    ]
    rar_parts = [
        f for f in files
        if RAR_VOLUME_PATTERN.search(f.name) and not _in_sample_dir(f, job_folder)
    ]

    if media_files:
        await _rename_obfuscated(job_folder, media_files, dry_run, results)
        if rar_parts:
            await _cleanup_redundant_rar_parts(job_folder, rar_parts, dry_run, results)
    elif rar_parts:
        await _extract_stuck_archive(job_folder, rar_parts, dry_run, results)
    # else: nothing usable and no archive to extract — likely still downloading
    # or genuinely empty; leave it alone.

    if remove_junk:
        for f in files:
            if not f.exists() or not is_junk_file(f):
                continue
            try:
                if not dry_run:
                    f.unlink()
                results["junk_removed"] += 1
                await create_cleanup_activity_log(
                    job_folder.name, f.name, None, "junk_removed", "Removed junk file", dry_run
                )
                results["actions"].append({
                    "job_folder": job_folder.name, "original": f.name,
                    "new": None, "action": "junk_removed", "message": "Removed junk file"
                })
            except OSError as e:
                results["errors"] += 1
                await create_cleanup_activity_log(
                    job_folder.name, f.name, None, "error", str(e), dry_run
                )


async def run_cleanup(dry_run: bool = False) -> dict:
    """Scan completed/<category>/<job-folder> and rescue unimported downloads."""
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
        "renamed": 0, "extracted": 0, "junk_removed": 0, "skipped": 0, "errors": 0, "actions": []
    }

    try:
        for category_dir in sorted(watch_folder.iterdir()):
            if not category_dir.is_dir() or category_dir.name.startswith(('_', '.')):
                continue

            for job_folder in sorted(category_dir.iterdir()):
                if not job_folder.is_dir() or job_folder.name.startswith(('_', '.')):
                    continue

                results["scanned"] += 1

                if _folder_is_settling(job_folder, min_age):
                    logger.info(f"Cleanup: skipping '{job_folder.name}' — modified within the last {min_age} minutes")
                    continue

                await _process_job_folder(job_folder, remove_junk, dry_run, results)

    except Exception as e:
        logger.error(f"Cleanup scan error: {e}")
        results["status"] = "error"
        results["reason"] = str(e)
    finally:
        cleanup_in_progress = False

    return results


def get_cleanup_status():
    return {"in_progress": cleanup_in_progress}
