import aiosqlite
import json
from pathlib import Path
from datetime import datetime

DATABASE_PATH = Path("/config/nas_sync.db")


async def init_db():
    """Initialize the database with required tables."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # NAS configuration table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS nas_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                hostname TEXT NOT NULL,
                ssh_user TEXT NOT NULL,
                ssh_key_path TEXT DEFAULT '/config/id_rsa',
                ssh_port INTEGER DEFAULT 22,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Folder mappings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS folder_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                delete_source INTEGER DEFAULT 0,
                last_sync_at TIMESTAMP,
                last_sync_status TEXT,
                last_sync_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Sync logs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_id INTEGER,
                status TEXT NOT NULL,
                message TEXT,
                files_transferred INTEGER DEFAULT 0,
                bytes_transferred INTEGER DEFAULT 0,
                duration_seconds REAL,
                started_at TIMESTAMP,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mapping_id) REFERENCES folder_mappings(id) ON DELETE CASCADE
            )
        """)

        # Scheduler config table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduler_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 1,
                interval_minutes INTEGER DEFAULT 15,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Post-sync actions table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS post_sync_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                action_type TEXT NOT NULL,
                config TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Insert default scheduler config if not exists
        await db.execute("""
            INSERT OR IGNORE INTO scheduler_config (id, enabled, interval_minutes)
            VALUES (1, 1, 15)
        """)

        await db.commit()


# NAS Config functions
async def get_nas_config():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM nas_config WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def save_nas_config(hostname: str, ssh_user: str, ssh_key_path: str = "/config/id_rsa", ssh_port: int = 22):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO nas_config (id, hostname, ssh_user, ssh_key_path, ssh_port, updated_at)
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                hostname = excluded.hostname,
                ssh_user = excluded.ssh_user,
                ssh_key_path = excluded.ssh_key_path,
                ssh_port = excluded.ssh_port,
                updated_at = CURRENT_TIMESTAMP
        """, (hostname, ssh_user, ssh_key_path, ssh_port))
        await db.commit()


# Folder mapping functions
async def get_folder_mappings():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM folder_mappings ORDER BY name") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_folder_mapping(mapping_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM folder_mappings WHERE id = ?", (mapping_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_folder_mapping(name: str, source_path: str, destination_path: str, delete_source: bool = False):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO folder_mappings (name, source_path, destination_path, delete_source)
            VALUES (?, ?, ?, ?)
        """, (name, source_path, destination_path, int(delete_source)))
        await db.commit()
        return cursor.lastrowid


async def update_folder_mapping(mapping_id: int, name: str, source_path: str, destination_path: str, 
                                 enabled: bool, delete_source: bool):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE folder_mappings 
            SET name = ?, source_path = ?, destination_path = ?, enabled = ?, delete_source = ?
            WHERE id = ?
        """, (name, source_path, destination_path, int(enabled), int(delete_source), mapping_id))
        await db.commit()


async def delete_folder_mapping(mapping_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM folder_mappings WHERE id = ?", (mapping_id,))
        await db.commit()


async def update_mapping_sync_status(mapping_id: int, status: str, message: str = None):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE folder_mappings 
            SET last_sync_at = CURRENT_TIMESTAMP, last_sync_status = ?, last_sync_message = ?
            WHERE id = ?
        """, (status, message, mapping_id))
        await db.commit()


# Sync log functions
async def create_sync_log(mapping_id: int, status: str, message: str = None, 
                          files_transferred: int = 0, bytes_transferred: int = 0,
                          duration_seconds: float = None, started_at: str = None):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO sync_logs (mapping_id, status, message, files_transferred, 
                                   bytes_transferred, duration_seconds, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (mapping_id, status, message, files_transferred, bytes_transferred, duration_seconds, started_at))
        await db.commit()


async def get_recent_sync_logs(limit: int = 50):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT sl.*, fm.name as mapping_name 
            FROM sync_logs sl
            LEFT JOIN folder_mappings fm ON sl.mapping_id = fm.id
            ORDER BY sl.completed_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_mapping_sync_logs(mapping_id: int, limit: int = 20):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM sync_logs 
            WHERE mapping_id = ?
            ORDER BY completed_at DESC
            LIMIT ?
        """, (mapping_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# Scheduler config functions
async def get_scheduler_config():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scheduler_config WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {"enabled": True, "interval_minutes": 15}


async def save_scheduler_config(enabled: bool, interval_minutes: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO scheduler_config (id, enabled, interval_minutes, updated_at)
            VALUES (1, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                interval_minutes = excluded.interval_minutes,
                updated_at = CURRENT_TIMESTAMP
        """, (int(enabled), interval_minutes))
        await db.commit()


# Post-sync actions functions
async def get_post_sync_actions():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM post_sync_actions ORDER BY name") as cursor:
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                row_dict = dict(row)
                row_dict['config'] = json.loads(row_dict['config'])
                result.append(row_dict)
            return result


async def create_post_sync_action(name: str, action_type: str, config: dict):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO post_sync_actions (name, action_type, config)
            VALUES (?, ?, ?)
        """, (name, action_type, json.dumps(config)))
        await db.commit()
        return cursor.lastrowid


async def update_post_sync_action(action_id: int, name: str, action_type: str, config: dict, enabled: bool):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE post_sync_actions 
            SET name = ?, action_type = ?, config = ?, enabled = ?
            WHERE id = ?
        """, (name, action_type, json.dumps(config), int(enabled), action_id))
        await db.commit()


async def delete_post_sync_action(action_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM post_sync_actions WHERE id = ?", (action_id,))
        await db.commit()
