import asyncio
import subprocess
import re
import httpx
from datetime import datetime
from typing import Optional, Tuple
import logging

from database import (
    get_nas_config, get_folder_mappings, update_mapping_sync_status,
    create_sync_log, get_post_sync_actions
)

logger = logging.getLogger(__name__)

# Global sync state
sync_in_progress = False
current_sync_mapping = None


async def check_nas_online(hostname: str, timeout: int = 2) -> bool:
    """Check if NAS is reachable via ping."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(timeout), hostname,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await process.wait()
        return process.returncode == 0
    except Exception as e:
        logger.error(f"Error checking NAS status: {e}")
        return False


async def test_ssh_connection(hostname: str, ssh_user: str, ssh_key_path: str, ssh_port: int = 22) -> Tuple[bool, str]:
    """Test SSH connection to NAS."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ssh",
            "-i", ssh_key_path,
            "-p", str(ssh_port),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            f"{ssh_user}@{hostname}",
            "echo 'Connection successful'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            return True, "SSH connection successful"
        else:
            error_msg = stderr.decode().strip() or "Unknown SSH error"
            return False, f"SSH connection failed: {error_msg}"
    except Exception as e:
        return False, f"SSH test error: {str(e)}"


def parse_rsync_output(output: str) -> dict:
    """Parse rsync output to extract transfer statistics."""
    stats = {
        "files_transferred": 0,
        "bytes_transferred": 0
    }
    
    # Match patterns like "sent 1,234 bytes" or "Number of files transferred: 5"
    bytes_match = re.search(r'sent ([\d,]+) bytes', output)
    if bytes_match:
        stats["bytes_transferred"] = int(bytes_match.group(1).replace(",", ""))
    
    files_match = re.search(r'Number of regular files transferred: (\d+)', output)
    if files_match:
        stats["files_transferred"] = int(files_match.group(1))
    
    return stats


async def run_rsync(source: str, destination: str, ssh_user: str, hostname: str, 
                    ssh_key_path: str, ssh_port: int = 22, delete_source: bool = False) -> Tuple[bool, str, dict]:
    """Run rsync to sync files from source to destination on remote NAS."""
    
    # Build rsync command
    ssh_cmd = f"ssh -i {ssh_key_path} -p {ssh_port} -o StrictHostKeyChecking=accept-new"
    
    # Ensure source path ends with / to sync contents
    if not source.endswith('/'):
        source = source + '/'
    
    cmd = [
        "rsync",
        "-avz",
        "--stats",
        "--progress",
        "-e", ssh_cmd,
    ]
    
    if delete_source:
        cmd.append("--remove-source-files")
    
    # Remote destination
    remote_dest = f"{ssh_user}@{hostname}:{destination}"
    cmd.extend([source, remote_dest])
    
    logger.info(f"Running rsync: {' '.join(cmd)}")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        output = stdout.decode()
        error_output = stderr.decode()
        
        if process.returncode == 0:
            stats = parse_rsync_output(output)
            return True, "Sync completed successfully", stats
        else:
            error_msg = error_output.strip() or output.strip() or "Unknown rsync error"
            return False, f"Rsync failed: {error_msg}", {"files_transferred": 0, "bytes_transferred": 0}
            
    except Exception as e:
        logger.error(f"Rsync execution error: {e}")
        return False, f"Rsync error: {str(e)}", {"files_transferred": 0, "bytes_transferred": 0}


async def execute_post_sync_actions():
    """Execute all enabled post-sync actions."""
    actions = await get_post_sync_actions()
    
    for action in actions:
        if not action['enabled']:
            continue
            
        try:
            if action['action_type'] == 'plex_refresh':
                await execute_plex_refresh(action['config'])
            elif action['action_type'] == 'webhook':
                await execute_webhook(action['config'])
        except Exception as e:
            logger.error(f"Post-sync action '{action['name']}' failed: {e}")


async def execute_plex_refresh(config: dict):
    """Trigger Plex library refresh."""
    plex_url = config.get('plex_url', '').rstrip('/')
    plex_token = config.get('plex_token', '')
    library_section = config.get('library_section', '1')
    
    if not plex_url or not plex_token:
        logger.warning("Plex refresh skipped: missing URL or token")
        return
    
    url = f"{plex_url}/library/sections/{library_section}/refresh?X-Plex-Token={plex_token}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10)
        if response.status_code == 200:
            logger.info(f"Plex library section {library_section} refresh triggered")
        else:
            logger.warning(f"Plex refresh returned status {response.status_code}")


async def execute_webhook(config: dict):
    """Execute a generic webhook."""
    url = config.get('url', '')
    method = config.get('method', 'POST').upper()
    
    if not url:
        logger.warning("Webhook skipped: missing URL")
        return
    
    async with httpx.AsyncClient() as client:
        if method == 'GET':
            response = await client.get(url, timeout=10)
        else:
            response = await client.post(url, timeout=10)
        
        logger.info(f"Webhook {url} returned status {response.status_code}")


async def sync_mapping(mapping: dict, nas_config: dict) -> bool:
    """Sync a single folder mapping."""
    global current_sync_mapping
    
    mapping_id = mapping['id']
    current_sync_mapping = mapping_id
    started_at = datetime.utcnow().isoformat()
    
    logger.info(f"Starting sync for mapping '{mapping['name']}': {mapping['source_path']} -> {mapping['destination_path']}")
    
    try:
        start_time = datetime.utcnow()
        
        success, message, stats = await run_rsync(
            source=mapping['source_path'],
            destination=mapping['destination_path'],
            ssh_user=nas_config['ssh_user'],
            hostname=nas_config['hostname'],
            ssh_key_path=nas_config['ssh_key_path'],
            ssh_port=nas_config['ssh_port'],
            delete_source=bool(mapping['delete_source'])
        )
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        status = "success" if success else "error"
        
        # Update mapping status
        await update_mapping_sync_status(mapping_id, status, message)
        
        # Create sync log
        await create_sync_log(
            mapping_id=mapping_id,
            status=status,
            message=message,
            files_transferred=stats['files_transferred'],
            bytes_transferred=stats['bytes_transferred'],
            duration_seconds=duration,
            started_at=started_at
        )
        
        return success
        
    except Exception as e:
        logger.error(f"Error syncing mapping {mapping_id}: {e}")
        await update_mapping_sync_status(mapping_id, "error", str(e))
        await create_sync_log(
            mapping_id=mapping_id,
            status="error",
            message=str(e),
            started_at=started_at
        )
        return False
    finally:
        current_sync_mapping = None


async def run_sync_all():
    """Run sync for all enabled mappings if NAS is online."""
    global sync_in_progress
    
    if sync_in_progress:
        logger.info("Sync already in progress, skipping")
        return {"status": "skipped", "reason": "Sync already in progress"}
    
    nas_config = await get_nas_config()
    if not nas_config:
        logger.warning("No NAS configuration found")
        return {"status": "error", "reason": "No NAS configuration"}
    
    # Check if NAS is online
    if not await check_nas_online(nas_config['hostname']):
        logger.info(f"NAS {nas_config['hostname']} is offline, skipping sync")
        return {"status": "skipped", "reason": "NAS is offline"}
    
    mappings = await get_folder_mappings()
    enabled_mappings = [m for m in mappings if m['enabled']]
    
    if not enabled_mappings:
        logger.info("No enabled mappings to sync")
        return {"status": "skipped", "reason": "No enabled mappings"}
    
    sync_in_progress = True
    results = {"status": "completed", "mappings": [], "any_synced": False}
    
    try:
        for mapping in enabled_mappings:
            success = await sync_mapping(mapping, nas_config)
            results["mappings"].append({
                "id": mapping['id'],
                "name": mapping['name'],
                "success": success
            })
            if success:
                results["any_synced"] = True
        
        # Run post-sync actions if anything was synced
        if results["any_synced"]:
            await execute_post_sync_actions()
            
    finally:
        sync_in_progress = False
    
    return results


async def run_sync_single(mapping_id: int):
    """Run sync for a single mapping."""
    global sync_in_progress
    
    if sync_in_progress:
        return {"status": "error", "reason": "Sync already in progress"}
    
    nas_config = await get_nas_config()
    if not nas_config:
        return {"status": "error", "reason": "No NAS configuration"}
    
    # Check if NAS is online
    if not await check_nas_online(nas_config['hostname']):
        return {"status": "error", "reason": "NAS is offline"}
    
    from database import get_folder_mapping
    mapping = await get_folder_mapping(mapping_id)
    if not mapping:
        return {"status": "error", "reason": "Mapping not found"}
    
    sync_in_progress = True
    
    try:
        success = await sync_mapping(mapping, nas_config)
        
        if success:
            await execute_post_sync_actions()
        
        return {
            "status": "completed" if success else "error",
            "mapping": mapping['name'],
            "success": success
        }
    finally:
        sync_in_progress = False


def get_sync_status():
    """Get current sync status."""
    return {
        "in_progress": sync_in_progress,
        "current_mapping": current_sync_mapping
    }
