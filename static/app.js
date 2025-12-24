// State
let currentTab = 'dashboard';
let nasOnline = false;
let syncInProgress = false;

// API helpers
async function api(endpoint, method = 'GET', data = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (data) {
        options.body = JSON.stringify(data);
    }
    const response = await fetch(`/api${endpoint}`, options);
    return response.json();
}

// Toast notifications
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// Tab navigation
function switchTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Load tab-specific data
    if (tabName === 'dashboard') loadDashboard();
    else if (tabName === 'mappings') loadMappings();
    else if (tabName === 'logs') loadLogs();
    else if (tabName === 'settings') loadSettings();
}

// Modal helpers
function openModal(modalId) {
    document.getElementById(modalId).classList.add('active');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}

// Format helpers
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString();
}

function formatDuration(seconds) {
    if (!seconds) return '-';
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
}

// Dashboard
async function loadDashboard() {
    await updateNasStatus();
    await loadDashboardStats();
    await loadRecentLogs();
}

async function updateNasStatus() {
    const data = await api('/nas-status');
    nasOnline = data.online;
    
    const dot = document.getElementById('nas-status-dot');
    const text = document.getElementById('nas-status-text');
    
    if (!data.configured) {
        dot.className = 'status-dot';
        text.textContent = 'Not configured';
    } else if (data.online) {
        dot.className = 'status-dot online';
        text.textContent = `${data.hostname} - Online`;
    } else {
        dot.className = 'status-dot';
        text.textContent = `${data.hostname} - Offline`;
    }
    
    // Check sync status
    const syncData = await api('/sync/status');
    if (syncData.sync.in_progress) {
        dot.className = 'status-dot syncing';
        text.textContent += ' (Syncing...)';
    }
    
    // Update scheduler info
    const nextRun = document.getElementById('next-sync-time');
    if (syncData.scheduler.next_run) {
        nextRun.textContent = `Next sync: ${formatDate(syncData.scheduler.next_run)}`;
    } else {
        nextRun.textContent = 'Scheduler disabled';
    }
}

async function loadDashboardStats() {
    const mappings = await api('/mappings');
    const logs = await api('/logs?limit=100');
    
    const enabledCount = mappings.mappings.filter(m => m.enabled).length;
    const successCount = logs.logs.filter(l => l.status === 'success').length;
    const errorCount = logs.logs.filter(l => l.status === 'error').length;
    
    document.getElementById('stat-mappings').textContent = mappings.mappings.length;
    document.getElementById('stat-enabled').textContent = enabledCount;
    document.getElementById('stat-success').textContent = successCount;
    document.getElementById('stat-errors').textContent = errorCount;
}

async function loadRecentLogs() {
    const data = await api('/logs?limit=10');
    const container = document.getElementById('recent-logs');
    
    if (data.logs.length === 0) {
        container.innerHTML = '<div class="empty-state">No sync logs yet</div>';
        return;
    }
    
    container.innerHTML = data.logs.map(log => `
        <div class="log-entry">
            <span class="log-time">${formatDate(log.completed_at)}</span>
            <span class="badge badge-${log.status === 'success' ? 'success' : 'error'}">${log.status}</span>
            <span class="log-message">${log.mapping_name || 'Unknown'} - ${log.message || ''}</span>
        </div>
    `).join('');
}

// Mappings
async function loadMappings() {
    const data = await api('/mappings');
    const container = document.getElementById('mappings-list');
    
    if (data.mappings.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📁</div>
                <p>No folder mappings configured</p>
                <button class="btn btn-primary" onclick="openAddMappingModal()">Add Mapping</button>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Source</th>
                    <th>Destination</th>
                    <th>Status</th>
                    <th>Last Sync</th>
                    <th>Enabled</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${data.mappings.map(m => `
                    <tr>
                        <td><strong>${m.name}</strong></td>
                        <td class="path">${m.source_path}</td>
                        <td class="path">${m.destination_path}</td>
                        <td>
                            ${m.last_sync_status ? 
                                `<span class="badge badge-${m.last_sync_status === 'success' ? 'success' : 'error'}">${m.last_sync_status}</span>` : 
                                '<span class="badge badge-info">Never</span>'}
                        </td>
                        <td>${formatDate(m.last_sync_at)}</td>
                        <td>
                            <label class="toggle">
                                <input type="checkbox" ${m.enabled ? 'checked' : ''} onchange="toggleMapping(${m.id}, this.checked)">
                                <span class="toggle-slider"></span>
                            </label>
                        </td>
                        <td class="action-btns">
                            <button class="btn btn-sm btn-success" onclick="syncMapping(${m.id})" title="Sync now">▶</button>
                            <button class="btn btn-sm btn-secondary" onclick="editMapping(${m.id})" title="Edit">✏️</button>
                            <button class="btn btn-sm btn-danger" onclick="deleteMapping(${m.id})" title="Delete">🗑️</button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function openAddMappingModal() {
    document.getElementById('mapping-modal-title').textContent = 'Add Folder Mapping';
    document.getElementById('mapping-form').reset();
    document.getElementById('mapping-id').value = '';
    openModal('mapping-modal');
}

async function editMapping(id) {
    const data = await api(`/mappings/${id}`);
    const m = data.mapping;
    
    document.getElementById('mapping-modal-title').textContent = 'Edit Folder Mapping';
    document.getElementById('mapping-id').value = m.id;
    document.getElementById('mapping-name').value = m.name;
    document.getElementById('mapping-source').value = m.source_path;
    document.getElementById('mapping-dest').value = m.destination_path;
    document.getElementById('mapping-enabled').checked = m.enabled;
    document.getElementById('mapping-delete-source').checked = m.delete_source;
    
    openModal('mapping-modal');
}

async function saveMapping() {
    const id = document.getElementById('mapping-id').value;
    const data = {
        name: document.getElementById('mapping-name').value,
        source_path: document.getElementById('mapping-source').value,
        destination_path: document.getElementById('mapping-dest').value,
        enabled: document.getElementById('mapping-enabled').checked,
        delete_source: document.getElementById('mapping-delete-source').checked
    };
    
    if (!data.name || !data.source_path || !data.destination_path) {
        showToast('Please fill in all required fields', 'error');
        return;
    }
    
    if (id) {
        await api(`/mappings/${id}`, 'PUT', data);
        showToast('Mapping updated');
    } else {
        await api('/mappings', 'POST', data);
        showToast('Mapping created');
    }
    
    closeModal('mapping-modal');
    loadMappings();
}

async function toggleMapping(id, enabled) {
    const data = await api(`/mappings/${id}`);
    const m = data.mapping;
    
    await api(`/mappings/${id}`, 'PUT', {
        name: m.name,
        source_path: m.source_path,
        destination_path: m.destination_path,
        enabled: enabled,
        delete_source: m.delete_source
    });
    
    showToast(`Mapping ${enabled ? 'enabled' : 'disabled'}`);
}

async function deleteMapping(id) {
    if (!confirm('Are you sure you want to delete this mapping?')) return;
    
    await api(`/mappings/${id}`, 'DELETE');
    showToast('Mapping deleted');
    loadMappings();
}

async function syncMapping(id) {
    showToast('Starting sync...');
    const result = await api(`/sync/run/${id}`, 'POST');
    
    if (result.status === 'completed') {
        showToast('Sync completed successfully');
    } else {
        showToast(`Sync failed: ${result.reason || 'Unknown error'}`, 'error');
    }
    
    loadMappings();
    updateNasStatus();
}

async function syncAll() {
    showToast('Starting sync for all mappings...');
    const result = await api('/sync/run', 'POST');
    
    if (result.status === 'completed') {
        const successCount = result.mappings.filter(m => m.success).length;
        showToast(`Sync completed: ${successCount}/${result.mappings.length} successful`);
    } else {
        showToast(`Sync skipped: ${result.reason}`, 'error');
    }
    
    loadMappings();
    updateNasStatus();
}

// Logs
async function loadLogs() {
    const data = await api('/logs?limit=100');
    const container = document.getElementById('logs-list');
    
    if (data.logs.length === 0) {
        container.innerHTML = '<div class="empty-state">No sync logs yet</div>';
        return;
    }
    
    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Mapping</th>
                    <th>Status</th>
                    <th>Files</th>
                    <th>Size</th>
                    <th>Duration</th>
                    <th>Message</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(log => `
                    <tr>
                        <td>${formatDate(log.completed_at)}</td>
                        <td>${log.mapping_name || '-'}</td>
                        <td><span class="badge badge-${log.status === 'success' ? 'success' : 'error'}">${log.status}</span></td>
                        <td>${log.files_transferred || 0}</td>
                        <td>${formatBytes(log.bytes_transferred || 0)}</td>
                        <td>${formatDuration(log.duration_seconds)}</td>
                        <td class="path">${log.message || '-'}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// Settings
async function loadSettings() {
    await loadNasConfig();
    await loadSchedulerConfig();
    await loadPostSyncActions();
}

async function loadNasConfig() {
    const data = await api('/nas-config');
    const config = data.config;
    
    if (config) {
        document.getElementById('nas-hostname').value = config.hostname || '';
        document.getElementById('nas-user').value = config.ssh_user || '';
        document.getElementById('nas-key-path').value = config.ssh_key_path || '/config/id_rsa';
        document.getElementById('nas-port').value = config.ssh_port || 22;
    }
}

async function saveNasConfig() {
    const data = {
        hostname: document.getElementById('nas-hostname').value,
        ssh_user: document.getElementById('nas-user').value,
        ssh_key_path: document.getElementById('nas-key-path').value,
        ssh_port: parseInt(document.getElementById('nas-port').value) || 22
    };
    
    if (!data.hostname || !data.ssh_user) {
        showToast('Hostname and SSH user are required', 'error');
        return;
    }
    
    await api('/nas-config', 'POST', data);
    showToast('NAS configuration saved');
    updateNasStatus();
}

async function testNasConnection() {
    showToast('Testing connection...');
    const result = await api('/nas-test', 'POST');
    
    if (result.success) {
        showToast('Connection successful!');
    } else {
        showToast(`Connection failed: ${result.message}`, 'error');
    }
}

async function loadSchedulerConfig() {
    const data = await api('/scheduler');
    
    document.getElementById('scheduler-enabled').checked = data.config.enabled;
    document.getElementById('scheduler-interval').value = data.config.interval_minutes;
}

async function saveSchedulerConfig() {
    const data = {
        enabled: document.getElementById('scheduler-enabled').checked,
        interval_minutes: parseInt(document.getElementById('scheduler-interval').value) || 15
    };
    
    await api('/scheduler', 'POST', data);
    showToast('Scheduler settings saved');
    updateNasStatus();
}

async function loadPostSyncActions() {
    const data = await api('/actions');
    const container = document.getElementById('actions-list');
    
    if (data.actions.length === 0) {
        container.innerHTML = '<p class="empty-state">No post-sync actions configured</p>';
        return;
    }
    
    container.innerHTML = data.actions.map(action => `
        <div class="card" style="margin-bottom: 10px;">
            <div class="card-header">
                <span><strong>${action.name}</strong> <span class="badge badge-info">${action.action_type}</span></span>
                <div class="action-btns">
                    <label class="toggle">
                        <input type="checkbox" ${action.enabled ? 'checked' : ''} onchange="toggleAction(${action.id}, this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                    <button class="btn btn-sm btn-danger" onclick="deleteAction(${action.id})">🗑️</button>
                </div>
            </div>
        </div>
    `).join('');
}

function openAddActionModal() {
    document.getElementById('action-form').reset();
    document.getElementById('action-id').value = '';
    updateActionConfigFields();
    openModal('action-modal');
}

function updateActionConfigFields() {
    const type = document.getElementById('action-type').value;
    const container = document.getElementById('action-config-fields');
    
    if (type === 'plex_refresh') {
        container.innerHTML = `
            <div class="form-group">
                <label>Plex URL</label>
                <input type="text" id="action-plex-url" placeholder="http://192.168.1.100:32400">
            </div>
            <div class="form-group">
                <label>Plex Token</label>
                <input type="text" id="action-plex-token" placeholder="Your Plex token">
            </div>
            <div class="form-group">
                <label>Library Section ID</label>
                <input type="text" id="action-plex-section" value="1" placeholder="1">
            </div>
        `;
    } else if (type === 'webhook') {
        container.innerHTML = `
            <div class="form-group">
                <label>Webhook URL</label>
                <input type="text" id="action-webhook-url" placeholder="https://...">
            </div>
            <div class="form-group">
                <label>Method</label>
                <select id="action-webhook-method">
                    <option value="POST">POST</option>
                    <option value="GET">GET</option>
                </select>
            </div>
        `;
    }
}

async function saveAction() {
    const id = document.getElementById('action-id').value;
    const type = document.getElementById('action-type').value;
    
    let config = {};
    if (type === 'plex_refresh') {
        config = {
            plex_url: document.getElementById('action-plex-url').value,
            plex_token: document.getElementById('action-plex-token').value,
            library_section: document.getElementById('action-plex-section').value || '1'
        };
    } else if (type === 'webhook') {
        config = {
            url: document.getElementById('action-webhook-url').value,
            method: document.getElementById('action-webhook-method').value
        };
    }
    
    const data = {
        name: document.getElementById('action-name').value,
        action_type: type,
        config: config,
        enabled: true
    };
    
    if (!data.name) {
        showToast('Please enter a name', 'error');
        return;
    }
    
    if (id) {
        await api(`/actions/${id}`, 'PUT', data);
        showToast('Action updated');
    } else {
        await api('/actions', 'POST', data);
        showToast('Action created');
    }
    
    closeModal('action-modal');
    loadPostSyncActions();
}

async function toggleAction(id, enabled) {
    // Get current action data and update
    const actions = await api('/actions');
    const action = actions.actions.find(a => a.id === id);
    if (action) {
        await api(`/actions/${id}`, 'PUT', {
            name: action.name,
            action_type: action.action_type,
            config: action.config,
            enabled: enabled
        });
        showToast(`Action ${enabled ? 'enabled' : 'disabled'}`);
    }
}

async function deleteAction(id) {
    if (!confirm('Delete this action?')) return;
    await api(`/actions/${id}`, 'DELETE');
    showToast('Action deleted');
    loadPostSyncActions();
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Tab click handlers
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });
    
    // Close modals on backdrop click
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal(modal.id);
        });
    });
    
    // Load initial data
    loadDashboard();
    
    // Auto-refresh status every 30 seconds
    setInterval(() => {
        if (currentTab === 'dashboard') {
            updateNasStatus();
            loadRecentLogs();
        }
    }, 30000);
});
