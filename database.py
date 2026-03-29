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

        # F1 organizer config table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS f1_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                watch_folder TEXT NOT NULL DEFAULT '',
                output_folder TEXT NOT NULL DEFAULT '',
                tvdb_api_key TEXT NOT NULL DEFAULT '',
                enabled INTEGER DEFAULT 0,
                scan_interval_minutes INTEGER DEFAULT 15,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # F1 episode cache from TheTVDB
        await db.execute("""
            CREATE TABLE IF NOT EXISTS f1_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season INTEGER NOT NULL,
                episode_number INTEGER NOT NULL,
                episode_name TEXT NOT NULL,
                air_date TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(season, episode_number)
            )
        """)

        # F1 activity log
        await db.execute("""
            CREATE TABLE IF NOT EXISTS f1_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                new_filename TEXT,
                season INTEGER,
                episode_number INTEGER,
                status TEXT NOT NULL,
                message TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Insert default F1 config if not exists
        await db.execute("""
            INSERT OR IGNORE INTO f1_config (id, watch_folder, output_folder, tvdb_api_key)
            VALUES (1, '', '', '')
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


# F1 Config functions
async def get_f1_config():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM f1_config WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {
                "watch_folder": "", "output_folder": "", "tvdb_api_key": "",
                "enabled": 0, "scan_interval_minutes": 15
            }


async def save_f1_config(watch_folder: str, output_folder: str, tvdb_api_key: str,
                         enabled: bool, scan_interval_minutes: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO f1_config (id, watch_folder, output_folder, tvdb_api_key, enabled, scan_interval_minutes, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                watch_folder = excluded.watch_folder,
                output_folder = excluded.output_folder,
                tvdb_api_key = excluded.tvdb_api_key,
                enabled = excluded.enabled,
                scan_interval_minutes = excluded.scan_interval_minutes,
                updated_at = CURRENT_TIMESTAMP
        """, (watch_folder, output_folder, tvdb_api_key, int(enabled), scan_interval_minutes))
        await db.commit()


# F1 Episode cache functions
async def get_f1_episodes(season: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM f1_episodes WHERE season = ? ORDER BY episode_number",
            (season,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def has_f1_season_cache(season: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM f1_episodes WHERE season = ?", (season,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] > 0


async def save_f1_episodes(season: int, episodes: list):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Clear existing cache for this season
        await db.execute("DELETE FROM f1_episodes WHERE season = ?", (season,))
        for ep in episodes:
            await db.execute("""
                INSERT INTO f1_episodes (season, episode_number, episode_name, air_date)
                VALUES (?, ?, ?, ?)
            """, (season, ep['episode_number'], ep['episode_name'], ep.get('air_date')))
        await db.commit()


async def clear_f1_episode_cache(season: int = None):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        if season:
            await db.execute("DELETE FROM f1_episodes WHERE season = ?", (season,))
        else:
            await db.execute("DELETE FROM f1_episodes")
        await db.commit()


# F1 Activity log functions
async def create_f1_activity_log(original_filename: str, new_filename: str = None,
                                  season: int = None, episode_number: int = None,
                                  status: str = "moved", message: str = None):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO f1_activity_log (original_filename, new_filename, season, episode_number, status, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (original_filename, new_filename, season, episode_number, status, message))
        await db.commit()


async def get_f1_activity_log(limit: int = 50):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM f1_activity_log ORDER BY processed_at DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
