import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

from database import (
    init_db, get_nas_config, save_nas_config,
    get_folder_mappings, get_folder_mapping, create_folder_mapping,
    update_folder_mapping, delete_folder_mapping,
    get_recent_sync_logs, get_mapping_sync_logs,
    get_scheduler_config, save_scheduler_config,
    get_post_sync_actions, create_post_sync_action,
    update_post_sync_action, delete_post_sync_action
)
from sync_engine import (
    check_nas_online, test_ssh_connection, run_sync_all,
    run_sync_single, get_sync_status
)
from scheduler import (
    start_scheduler, stop_scheduler, update_scheduler,
    get_scheduler_status
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting NAS Sync application")
    Path("/config").mkdir(parents=True, exist_ok=True)
    await init_db()
    start_scheduler()
    await update_scheduler()
    yield
    # Shutdown
    stop_scheduler()
    logger.info("NAS Sync application stopped")


app = FastAPI(title="NAS Sync", lifespan=lifespan)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Pydantic models for request validation
class NASConfigRequest(BaseModel):
    hostname: str
    ssh_user: str
    ssh_key_path: str = "/config/id_rsa"
    ssh_port: int = 22


class FolderMappingRequest(BaseModel):
    name: str
    source_path: str
    destination_path: str
    enabled: bool = True
    delete_source: bool = False


class SchedulerConfigRequest(BaseModel):
    enabled: bool
    interval_minutes: int


class PostSyncActionRequest(BaseModel):
    name: str
    action_type: str
    config: dict
    enabled: bool = True


# Web UI route
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# NAS Config API
@app.get("/api/nas-config")
async def api_get_nas_config():
    config = await get_nas_config()
    return {"config": config}


@app.post("/api/nas-config")
async def api_save_nas_config(config: NASConfigRequest):
    await save_nas_config(
        hostname=config.hostname,
        ssh_user=config.ssh_user,
        ssh_key_path=config.ssh_key_path,
        ssh_port=config.ssh_port
    )
    return {"status": "ok"}


@app.get("/api/nas-status")
async def api_get_nas_status():
    config = await get_nas_config()
    if not config:
        return {"online": False, "configured": False}
    
    online = await check_nas_online(config['hostname'])
    return {"online": online, "configured": True, "hostname": config['hostname']}


@app.post("/api/nas-test")
async def api_test_nas_connection():
    config = await get_nas_config()
    if not config:
        return {"success": False, "message": "NAS not configured"}
    
    success, message = await test_ssh_connection(
        hostname=config['hostname'],
        ssh_user=config['ssh_user'],
        ssh_key_path=config['ssh_key_path'],
        ssh_port=config['ssh_port']
    )
    return {"success": success, "message": message}


# Folder Mappings API
@app.get("/api/mappings")
async def api_get_mappings():
    mappings = await get_folder_mappings()
    return {"mappings": mappings}


@app.get("/api/mappings/{mapping_id}")
async def api_get_mapping(mapping_id: int):
    mapping = await get_folder_mapping(mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"mapping": mapping}


@app.post("/api/mappings")
async def api_create_mapping(mapping: FolderMappingRequest):
    mapping_id = await create_folder_mapping(
        name=mapping.name,
        source_path=mapping.source_path,
        destination_path=mapping.destination_path,
        delete_source=mapping.delete_source
    )
    return {"status": "ok", "id": mapping_id}


@app.put("/api/mappings/{mapping_id}")
async def api_update_mapping(mapping_id: int, mapping: FolderMappingRequest):
    existing = await get_folder_mapping(mapping_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Mapping not found")
    
    await update_folder_mapping(
        mapping_id=mapping_id,
        name=mapping.name,
        source_path=mapping.source_path,
        destination_path=mapping.destination_path,
        enabled=mapping.enabled,
        delete_source=mapping.delete_source
    )
    return {"status": "ok"}


@app.delete("/api/mappings/{mapping_id}")
async def api_delete_mapping(mapping_id: int):
    await delete_folder_mapping(mapping_id)
    return {"status": "ok"}


# Sync API
@app.get("/api/sync/status")
async def api_sync_status():
    sync_status = get_sync_status()
    scheduler_status = get_scheduler_status()
    return {
        "sync": sync_status,
        "scheduler": scheduler_status
    }


@app.post("/api/sync/run")
async def api_run_sync():
    result = await run_sync_all()
    return result


@app.post("/api/sync/run/{mapping_id}")
async def api_run_sync_single(mapping_id: int):
    result = await run_sync_single(mapping_id)
    return result


# Logs API
@app.get("/api/logs")
async def api_get_logs(limit: int = 50):
    logs = await get_recent_sync_logs(limit)
    return {"logs": logs}


@app.get("/api/logs/mapping/{mapping_id}")
async def api_get_mapping_logs(mapping_id: int, limit: int = 20):
    logs = await get_mapping_sync_logs(mapping_id, limit)
    return {"logs": logs}


# Scheduler API
@app.get("/api/scheduler")
async def api_get_scheduler():
    config = await get_scheduler_config()
    status = get_scheduler_status()
    return {"config": config, "status": status}


@app.post("/api/scheduler")
async def api_save_scheduler(config: SchedulerConfigRequest):
    await save_scheduler_config(
        enabled=config.enabled,
        interval_minutes=config.interval_minutes
    )
    await update_scheduler()
    return {"status": "ok"}


# Post-sync Actions API
@app.get("/api/actions")
async def api_get_actions():
    actions = await get_post_sync_actions()
    return {"actions": actions}


@app.post("/api/actions")
async def api_create_action(action: PostSyncActionRequest):
    action_id = await create_post_sync_action(
        name=action.name,
        action_type=action.action_type,
        config=action.config
    )
    return {"status": "ok", "id": action_id}


@app.put("/api/actions/{action_id}")
async def api_update_action(action_id: int, action: PostSyncActionRequest):
    await update_post_sync_action(
        action_id=action_id,
        name=action.name,
        action_type=action.action_type,
        config=action.config,
        enabled=action.enabled
    )
    return {"status": "ok"}


@app.delete("/api/actions/{action_id}")
async def api_delete_action(action_id: int):
    await delete_post_sync_action(action_id)
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
