# NAS Sync

A lightweight Docker container with a web UI for syncing folders to a NAS using rsync over SSH. Designed for scenarios where your NAS isn't always online.

## Features

- **Web UI** for managing sync configurations
- **Multiple folder mappings** - sync different source folders to different NAS destinations
- **Automatic scheduling** - configurable sync interval
- **NAS online detection** - only syncs when NAS is reachable
- **Post-sync actions** - trigger Plex library refresh or custom webhooks
- **Sync logs** - track sync history and status
- **Delete after sync** - optionally remove source files after successful transfer

## Quick Start

### Option A: Use Pre-built Image (Recommended)

```yaml
# Add to your existing docker-compose.yml
services:
  nas-sync:
    image: ghcr.io/YOUR_USERNAME/nas-sync:latest
    container_name: nas-sync
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./nas-sync/config:/config
      - ./nas-sync-key:/config/id_rsa:ro
      - /path/to/downloads/movies:/data/movies
      - /path/to/downloads/tv:/data/tv
      - /path/to/downloads/isos:/data/isos
    environment:
      - TZ=Europe/London
```

### Option B: Build Locally

Clone the repo and build:

```bash
git clone https://github.com/YOUR_USERNAME/nas-sync.git
cd nas-sync
docker-compose up -d
```

---

### 1. Generate SSH Key

```bash
# Generate a dedicated SSH key for NAS access
ssh-keygen -t ed25519 -f ./nas-sync-key -N ""

# Copy the public key to your NAS
ssh-copy-id -i ./nas-sync-key.pub your-user@your-nas-ip
```

### 2. Configure docker-compose.yml

```yaml
version: '3.8'

services:
  nas-sync:
    build: .
    container_name: nas-sync
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      # Config and database
      - ./config:/config
      
      # SSH key (required)
      - ./nas-sync-key:/config/id_rsa:ro
      
      # Your download folders (adjust paths)
      - /path/to/downloads/movies:/data/movies
      - /path/to/downloads/tv:/data/tv
      - /path/to/downloads/isos:/data/isos
      
    environment:
      - TZ=Europe/London
```

### 3. Build and Run

```bash
docker-compose up -d
```

### 4. Access the Web UI

Open `http://your-server:8080` in your browser.

## Configuration

### NAS Settings

In the Settings tab, configure:

- **Hostname/IP**: Your NAS address (e.g., `192.168.1.100` or `nas.local`)
- **SSH User**: User account on the NAS
- **SSH Key Path**: Path to private key inside container (default: `/config/id_rsa`)
- **SSH Port**: SSH port (default: `22`)

### Folder Mappings

Add mappings to define what syncs where:

| Field | Description |
|-------|-------------|
| Name | Friendly name for the mapping |
| Source Path | Local path inside container (e.g., `/data/movies`) |
| Destination Path | Path on NAS (e.g., `/volume1/media/movies`) |
| Enabled | Toggle sync on/off |
| Delete Source | Remove files after successful sync |

### Scheduler

Configure automatic sync:

- **Enable/Disable**: Toggle automatic syncing
- **Interval**: How often to check and sync (in minutes)

The scheduler only runs sync when:
1. The NAS is online (responds to ping)
2. There are enabled mappings

### Post-Sync Actions

Trigger actions after successful syncs:

#### Plex Library Refresh

- **Plex URL**: Your Plex server URL (e.g., `http://192.168.1.100:32400`)
- **Plex Token**: Your [Plex authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
- **Library Section**: Section ID to refresh (default: `1`)

#### Generic Webhook

- **URL**: Webhook endpoint
- **Method**: GET or POST

## Synology NAS Setup

### Enable SSH

1. Control Panel → Terminal & SNMP
2. Enable SSH service
3. Set a custom port if desired

### User Permissions

Ensure your SSH user has write access to the destination folders:

1. Control Panel → Shared Folder
2. Edit permissions for target folders
3. Grant Read/Write to your SSH user

### Find Your Plex Token (if using Plex on Synology)

1. Open Plex Web UI
2. Play any media
3. Click the `...` menu → Get Info → View XML
4. Find `X-Plex-Token` in the URL

## Troubleshooting

### SSH Connection Failed

1. Test SSH manually from the container:
   ```bash
   docker exec -it nas-sync ssh -i /config/id_rsa user@nas-ip
   ```

2. Check key permissions:
   ```bash
   docker exec -it nas-sync ls -la /config/id_rsa
   ```

3. Verify key is authorized on NAS:
   ```bash
   cat ~/.ssh/authorized_keys  # on NAS
   ```

### NAS Shows Offline

1. Check if NAS responds to ping from container:
   ```bash
   docker exec -it nas-sync ping -c 1 nas-ip
   ```

2. Verify network connectivity between Docker host and NAS

### Rsync Fails

Check the sync logs in the web UI for detailed error messages.

Common issues:
- Destination path doesn't exist on NAS
- Insufficient permissions
- SSH key not accepted

## API Endpoints

The application exposes a REST API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/nas-config` | GET/POST | NAS configuration |
| `/api/nas-status` | GET | Check NAS online status |
| `/api/nas-test` | POST | Test SSH connection |
| `/api/mappings` | GET/POST | List/create folder mappings |
| `/api/mappings/{id}` | GET/PUT/DELETE | Manage specific mapping |
| `/api/sync/run` | POST | Trigger sync for all mappings |
| `/api/sync/run/{id}` | POST | Trigger sync for specific mapping |
| `/api/sync/status` | GET | Get current sync status |
| `/api/logs` | GET | Get sync logs |
| `/api/scheduler` | GET/POST | Scheduler configuration |
| `/api/actions` | GET/POST | Post-sync actions |

## License

MIT

---

## Integration with Servarr Stack

Example integration with Sonarr/Radarr:

```yaml
version: '3.8'

services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    # ... your sonarr config ...
    volumes:
      - /path/to/downloads:/downloads

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    # ... your radarr config ...
    volumes:
      - /path/to/downloads:/downloads

  nas-sync:
    image: ghcr.io/YOUR_USERNAME/nas-sync:latest
    container_name: nas-sync
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./nas-sync/config:/config
      - ./nas-sync-key:/config/id_rsa:ro
      # Mount the same download paths used by sonarr/radarr
      - /path/to/downloads/tv:/data/tv
      - /path/to/downloads/movies:/data/movies
    environment:
      - TZ=Europe/London
```

Then in the NAS Sync UI:
- Source: `/data/tv` → Destination: `/volume1/media/tv`
- Source: `/data/movies` → Destination: `/volume1/media/movies`
