// ============================================================
// NAS Sync — front-panel console
// ============================================================

// State
let currentTab = 'dashboard';
let nasOnline = false;
let syncInProgress = false;

// ---------- Inline SVG icons ----------
const ICONS = {
    play:    '<svg class="ic" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
    edit:    '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    trash:   '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>',
    folder:  '<svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>',
    inbox:   '<svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/></svg>',
    flag:    '<svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 22V4s1-1 4-1 4 2 7 2 4-1 4-1v10s-1 1-4 1-4-2-7-2-4 1-4 1"/></svg>'
};
function icon(name) { return ICONS[name] || ''; }

// ---------- API helper ----------
async function api(endpoint, method = 'GET', data = null) {
    const options = { method, headers: { 'Content-Type': 'application/json' } };
    if (data) options.body = JSON.stringify(data);
    try {
        const response = await fetch(`/api${endpoint}`, options);
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || `HTTP ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`API ${method} ${endpoint} failed:`, error);
        throw error;
    }
}

// ---------- Toasts ----------
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// ---------- HTML escaping ----------
function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---------- Tab navigation ----------
function switchTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');

    if (tabName === 'dashboard') loadDashboard();
    else if (tabName === 'mappings') loadMappings();
    else if (tabName === 'f1') loadF1();
    else if (tabName === 'cleanup') loadCleanup();
    else if (tabName === 'logs') loadLogs();
    else if (tabName === 'settings') loadSettings();
}

// ---------- Modals ----------
function openModal(modalId) { document.getElementById(modalId).classList.add('active'); }
function closeModal(modalId) { document.getElementById(modalId).classList.remove('active'); }

// ---------- Formatters ----------
function formatBytes(bytes) {
    if (!bytes) return '0 B';
    const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDate(dateStr) {
    if (!dateStr) return '—';
    const date = new Date(dateStr);
    if (isNaN(date)) return '—';
    return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatDuration(seconds) {
    if (!seconds) return '—';
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
}

// Human-friendly next-run: local time + relative hint
function formatNextRun(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    if (isNaN(d)) return null;
    const now = new Date();
    const diffMs = d - now;
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // day prefix
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const dayDiff = Math.round((target - dayStart) / 86400000);
    let dayLabel = '';
    if (dayDiff === 0) dayLabel = '';
    else if (dayDiff === 1) dayLabel = 'Tomorrow ';
    else dayLabel = d.toLocaleDateString([], { weekday: 'short' }) + ' ';

    // relative
    let rel;
    const mins = Math.round(diffMs / 60000);
    if (diffMs <= 0) rel = 'due now';
    else if (mins < 60) rel = `in ${mins} min`;
    else if (mins < 1440) rel = `in ${Math.round(mins / 60)} h`;
    else rel = `in ${Math.round(mins / 1440)} d`;

    return { abs: `${dayLabel}${time}`, rel };
}

// Paint an LED element by state
function setLed(el, state) {
    if (!el) return;
    el.className = 'led' + (state ? ` is-${state}` : '');
}

// Fill a "next run" readout line
function renderNextRun(elId, iso, active, label) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (active) { el.innerHTML = `<strong>Running now…</strong>`; return; }
    const n = formatNextRun(iso);
    if (!n) { el.innerHTML = `${label || 'Next run'}: not scheduled`; return; }
    el.innerHTML = `${label || 'Next run'}: <strong>${esc(n.abs)}</strong> · ${esc(n.rel)}`;
}

// Fill a compact rail readout
function renderRail(ledId, valId, iso, active) {
    const led = document.getElementById(ledId);
    const val = document.getElementById(valId);
    if (active) { setLed(led, 'active'); if (val) val.textContent = 'Running…'; return; }
    const n = formatNextRun(iso);
    if (!n) { setLed(led, ''); if (val) val.textContent = 'Off'; return; }
    setLed(led, 'standby');
    if (val) val.textContent = `${n.abs} · ${n.rel}`;
}

// ---------- Schedule mode wiring ----------
function bindScheduleMode(selectId, intervalWrapId, timeWrapId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const apply = () => {
        const m = sel.value;
        const iw = document.getElementById(intervalWrapId);
        const tw = document.getElementById(timeWrapId);
        if (iw) iw.classList.toggle('is-hidden', m !== 'interval');
        if (tw) tw.classList.toggle('is-hidden', m !== 'daily');
    };
    sel.addEventListener('change', apply);
    apply();
}

// ============================================================
// Dashboard
// ============================================================
async function loadDashboard() {
    await updateNasStatus();
    await loadDashboardPanel();
    await loadDashboardStats();
    await loadRecentLogs();
}

async function updateNasStatus() {
    const data = await api('/nas-status');
    nasOnline = data.online;

    const dot = document.getElementById('nas-status-dot');
    const text = document.getElementById('nas-status-text');

    if (!data.configured) {
        setLed(dot, '');
        text.textContent = 'Not configured';
    } else if (data.online) {
        setLed(dot, 'online');
        text.textContent = `${data.hostname} online`;
    } else {
        setLed(dot, 'error');
        text.textContent = `${data.hostname} offline`;
    }

    // Sync in progress?
    const syncData = await api('/sync/status');
    syncInProgress = syncData.sync.in_progress;
    if (syncInProgress) {
        setLed(dot, 'active');
        text.textContent = 'Syncing…';
    }

    // Rail: NAS tile
    const nasLed = document.getElementById('rail-nas-led');
    const nasVal = document.getElementById('rail-nas-val');
    if (nasLed && nasVal) {
        if (!data.configured) { setLed(nasLed, ''); nasVal.textContent = 'Not configured'; }
        else if (syncInProgress) { setLed(nasLed, 'active'); nasVal.textContent = 'Syncing…'; }
        else if (data.online) { setLed(nasLed, 'online'); nasVal.textContent = `${data.hostname}`; }
        else { setLed(nasLed, 'error'); nasVal.textContent = `${data.hostname} offline`; }
    }
}

async function loadDashboardPanel() {
    try {
        const data = await api('/scheduler');
        const s = data.status || {};
        renderRail('rail-sync-led', 'rail-sync-val', s.next_run, s.job_active);
        renderRail('rail-f1-led', 'rail-f1-val', s.f1_next_run, s.f1_job_active);
        renderRail('rail-cl-led', 'rail-cl-val', s.cleanup_next_run, s.cleanup_job_active);
    } catch (e) {
        // leave defaults
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
    const errEl = document.getElementById('stat-errors');
    errEl.textContent = errorCount;
    errEl.style.color = errorCount > 0 ? 'var(--error)' : 'var(--text)';
}

async function loadRecentLogs() {
    const data = await api('/logs?limit=10');
    const container = document.getElementById('recent-logs');

    if (data.logs.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>No sync activity yet.</p></div>';
        return;
    }

    container.innerHTML = data.logs.map(log => `
        <div class="log-entry">
            <span class="log-time">${formatDate(log.completed_at)}</span>
            <span class="badge badge-${log.status === 'success' ? 'success' : 'error'}">${esc(log.status)}</span>
            <span class="log-message"><strong>${esc(log.mapping_name || 'Unknown')}</strong> ${esc(log.message || '')}</span>
        </div>
    `).join('');
}

// ============================================================
// Mappings
// ============================================================
async function loadMappings() {
    const data = await api('/mappings');
    const container = document.getElementById('mappings-list');

    if (data.mappings.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                ${icon('folder')}
                <p>No folder mappings yet.</p>
                <button class="btn btn-primary btn-sm" onclick="openAddMappingModal()">Add mapping</button>
            </div>`;
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Name</th><th>Source</th><th>Destination</th>
                    <th>Status</th><th>Last sync</th><th>Enabled</th><th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${data.mappings.map(m => `
                    <tr>
                        <td><strong>${esc(m.name)}</strong></td>
                        <td class="path">${esc(m.source_path)}</td>
                        <td class="path">${esc(m.destination_path)}</td>
                        <td>${m.last_sync_status
                            ? `<span class="badge badge-${m.last_sync_status === 'success' ? 'success' : 'error'}">${esc(m.last_sync_status)}</span>`
                            : '<span class="badge badge-muted">never</span>'}</td>
                        <td class="log-time">${formatDate(m.last_sync_at)}</td>
                        <td>
                            <label class="toggle">
                                <input type="checkbox" ${m.enabled ? 'checked' : ''} onchange="toggleMapping(${m.id}, this.checked)">
                                <span class="toggle-slider"></span>
                            </label>
                        </td>
                        <td class="action-btns">
                            <button class="btn btn-sm btn-success btn-icon" onclick="syncMapping(${m.id})" title="Sync now">${icon('play')}</button>
                            <button class="btn btn-sm btn-secondary btn-icon" onclick="editMapping(${m.id})" title="Edit">${icon('edit')}</button>
                            <button class="btn btn-sm btn-danger btn-icon" onclick="deleteMapping(${m.id})" title="Delete">${icon('trash')}</button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

function openAddMappingModal() {
    document.getElementById('mapping-modal-title').textContent = 'Add folder mapping';
    document.getElementById('mapping-form').reset();
    document.getElementById('mapping-id').value = '';
    openModal('mapping-modal');
}

async function editMapping(id) {
    const data = await api(`/mappings/${id}`);
    const m = data.mapping;
    document.getElementById('mapping-modal-title').textContent = 'Edit folder mapping';
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
        showToast('Fill in name, source, and destination', 'error');
        return;
    }
    if (id) { await api(`/mappings/${id}`, 'PUT', data); showToast('Mapping updated'); }
    else { await api('/mappings', 'POST', data); showToast('Mapping created'); }
    closeModal('mapping-modal');
    loadMappings();
}

async function toggleMapping(id, enabled) {
    const data = await api(`/mappings/${id}`);
    const m = data.mapping;
    await api(`/mappings/${id}`, 'PUT', {
        name: m.name, source_path: m.source_path, destination_path: m.destination_path,
        enabled: enabled, delete_source: m.delete_source
    });
    showToast(`Mapping ${enabled ? 'enabled' : 'disabled'}`);
}

async function deleteMapping(id) {
    if (!confirm('Delete this mapping?')) return;
    await api(`/mappings/${id}`, 'DELETE');
    showToast('Mapping deleted');
    loadMappings();
}

async function syncMapping(id) {
    showToast('Starting sync…');
    const result = await api(`/sync/run/${id}`, 'POST');
    if (result.status === 'completed') showToast('Sync completed');
    else showToast(`Sync failed: ${result.reason || 'Unknown error'}`, 'error');
    loadMappings();
    updateNasStatus();
}

async function syncAll() {
    showToast('Starting sync for all mappings…');
    const result = await api('/sync/run', 'POST');
    if (result.status === 'completed') {
        const successCount = result.mappings.filter(m => m.success).length;
        showToast(`Sync completed: ${successCount}/${result.mappings.length} succeeded`);
    } else {
        showToast(`Sync skipped: ${result.reason}`, 'error');
    }
    if (currentTab === 'mappings') loadMappings();
    updateNasStatus();
    loadDashboardPanel();
}

// ============================================================
// Logs
// ============================================================
async function loadLogs() {
    const data = await api('/logs?limit=100');
    const container = document.getElementById('logs-list');

    if (data.logs.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>No sync logs yet.</p></div>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Time</th><th>Mapping</th><th>Status</th><th>Files</th><th>Size</th><th>Duration</th><th>Message</th></tr>
            </thead>
            <tbody>
                ${data.logs.map(log => `
                    <tr>
                        <td class="log-time">${formatDate(log.completed_at)}</td>
                        <td>${esc(log.mapping_name || '—')}</td>
                        <td><span class="badge badge-${log.status === 'success' ? 'success' : 'error'}">${esc(log.status)}</span></td>
                        <td>${log.files_transferred || 0}</td>
                        <td>${formatBytes(log.bytes_transferred || 0)}</td>
                        <td>${formatDuration(log.duration_seconds)}</td>
                        <td class="path">${esc(log.message || '—')}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

// ============================================================
// Settings
// ============================================================
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
    showToast('Testing connection…');
    const result = await api('/nas-test', 'POST');
    if (result.success) showToast('Connection successful');
    else showToast(`Connection failed: ${result.message}`, 'error');
}

async function loadSchedulerConfig() {
    const data = await api('/scheduler');
    const c = data.config || {};
    document.getElementById('scheduler-enabled').checked = !!c.enabled;
    document.getElementById('sched-mode').value = c.schedule_mode || 'interval';
    document.getElementById('scheduler-interval').value = c.interval_minutes || 15;
    document.getElementById('sched-daily-time').value = c.daily_time || '03:00';
    bindScheduleMode('sched-mode', 'sched-interval-wrap', 'sched-time-wrap');
    renderNextRun('sched-next-run', (data.status || {}).next_run, (data.status || {}).job_active, 'Next sync');
}

async function saveSchedulerConfig() {
    const data = {
        enabled: document.getElementById('scheduler-enabled').checked,
        schedule_mode: document.getElementById('sched-mode').value,
        interval_minutes: parseInt(document.getElementById('scheduler-interval').value) || 15,
        daily_time: document.getElementById('sched-daily-time').value || '03:00'
    };
    await api('/scheduler', 'POST', data);
    showToast('Sync schedule saved');
    loadSchedulerConfig();
    updateNasStatus();
    loadDashboardPanel();
}

async function loadPostSyncActions() {
    const data = await api('/actions');
    const container = document.getElementById('actions-list');
    if (data.actions.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>No post-sync actions configured.</p></div>';
        return;
    }
    container.innerHTML = data.actions.map(action => `
        <div class="action-row">
            <span class="action-row-name">${esc(action.name)} <span class="badge badge-info">${esc(action.action_type)}</span></span>
            <div class="action-btns">
                <label class="toggle">
                    <input type="checkbox" ${action.enabled ? 'checked' : ''} onchange="toggleAction(${action.id}, this.checked)">
                    <span class="toggle-slider"></span>
                </label>
                <button class="btn btn-sm btn-danger btn-icon" onclick="deleteAction(${action.id})" title="Delete">${icon('trash')}</button>
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
            <div class="form-group"><label>Plex URL</label><input type="text" id="action-plex-url" placeholder="http://192.168.1.100:32400"></div>
            <div class="form-group"><label>Plex token</label><input type="text" id="action-plex-token" placeholder="Your Plex token"></div>
            <div class="form-group"><label>Library section ID</label><input type="text" id="action-plex-section" value="1" placeholder="1"></div>`;
    } else if (type === 'webhook') {
        container.innerHTML = `
            <div class="form-group"><label>Webhook URL</label><input type="text" id="action-webhook-url" placeholder="https://…"></div>
            <div class="form-group"><label>Method</label><select id="action-webhook-method"><option value="POST">POST</option><option value="GET">GET</option></select></div>`;
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
    const data = { name: document.getElementById('action-name').value, action_type: type, config, enabled: true };
    if (!data.name) { showToast('Enter a name', 'error'); return; }
    if (id) { await api(`/actions/${id}`, 'PUT', data); showToast('Action updated'); }
    else { await api('/actions', 'POST', data); showToast('Action created'); }
    closeModal('action-modal');
    loadPostSyncActions();
}

async function toggleAction(id, enabled) {
    const actions = await api('/actions');
    const action = actions.actions.find(a => a.id === id);
    if (action) {
        await api(`/actions/${id}`, 'PUT', {
            name: action.name, action_type: action.action_type, config: action.config, enabled
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

// ============================================================
// F1 Organizer
// ============================================================
async function loadF1() {
    await loadF1Config();
    await loadF1Episodes();
    await loadF1Activity();
}

async function loadF1Config() {
    const data = await api('/f1/config');
    const config = data.config;
    document.getElementById('f1-watch-folder').value = config.watch_folder || '';
    document.getElementById('f1-output-folder').value = config.output_folder || '';
    document.getElementById('f1-tvdb-key').value = config.tvdb_api_key || '';
    document.getElementById('f1-scan-interval').value = config.scan_interval_minutes || 15;
    document.getElementById('f1-mode').value = config.schedule_mode || 'interval';
    document.getElementById('f1-daily-time').value = config.daily_time || '03:00';
    document.getElementById('f1-enabled').checked = !!config.enabled;
    bindScheduleMode('f1-mode', 'f1-interval-wrap', 'f1-time-wrap');

    const seasonInput = document.getElementById('f1-season-select');
    if (!seasonInput.value) seasonInput.value = new Date().getFullYear();

    // Next-run readout from scheduler status
    try {
        const sched = await api('/scheduler');
        renderNextRun('f1-next-run', (sched.status || {}).f1_next_run, (sched.status || {}).f1_job_active, 'Next scan');
    } catch (e) { /* ignore */ }
}

async function saveF1Config() {
    const data = {
        watch_folder: document.getElementById('f1-watch-folder').value,
        output_folder: document.getElementById('f1-output-folder').value,
        tvdb_api_key: document.getElementById('f1-tvdb-key').value,
        enabled: document.getElementById('f1-enabled').checked,
        scan_interval_minutes: parseInt(document.getElementById('f1-scan-interval').value) || 15,
        schedule_mode: document.getElementById('f1-mode').value,
        daily_time: document.getElementById('f1-daily-time').value || '03:00'
    };
    if (!data.watch_folder || !data.output_folder) {
        showToast('Watch folder and output folder are required', 'error');
        return;
    }
    try {
        await api('/f1/config', 'POST', data);
        showToast('F1 configuration saved');
        loadF1Config();
        loadDashboardPanel();
    } catch (error) {
        showToast(`Failed to save F1 config: ${error.message}`, 'error');
    }
}

async function loadF1Episodes() {
    const season = document.getElementById('f1-season-select').value || new Date().getFullYear();
    const data = await api(`/f1/episodes?season=${season}`);
    const container = document.getElementById('f1-episodes-list');

    if (!data.episodes || data.episodes.length === 0) {
        container.innerHTML = `<div class="empty-state">${icon('flag')}<p>No cached episodes for season ${esc(season)}. Add your TheTVDB API key and refresh.</p></div>`;
        return;
    }

    container.innerHTML = `
        <table>
            <thead><tr><th>Ep #</th><th>Episode name</th><th>Air date</th></tr></thead>
            <tbody>
                ${data.episodes.map(ep => `
                    <tr>
                        <td class="log-time">S${esc(season)}E${String(ep.episode_number).padStart(2, '0')}</td>
                        <td>${esc(ep.episode_name)}</td>
                        <td class="log-time">${esc(ep.air_date || '—')}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

async function loadF1Activity() {
    const filter = document.getElementById('f1-activity-filter').value;
    const params = filter ? `?status=${filter}` : '';
    const data = await api(`/f1/activity${params}`);
    const container = document.getElementById('f1-activity-list');

    if (!data.activity || data.activity.length === 0) {
        container.innerHTML = `<div class="empty-state">${icon('inbox')}<p>No activity yet. Run a scan to organize F1 episodes.</p></div>`;
        return;
    }

    const badgeFor = s => s === 'moved' ? 'success' : s === 'unmatched' || s === 'duplicate' ? 'warning' : 'error';

    container.innerHTML = `
        <table>
            <thead><tr><th>Time</th><th>Original file</th><th>New name</th><th>Status</th></tr></thead>
            <tbody>
                ${data.activity.map(a => `
                    <tr>
                        <td class="log-time">${formatDate(a.processed_at)}</td>
                        <td class="path ellip" title="${esc(a.original_filename)}">${esc(a.original_filename)}</td>
                        <td class="path ellip" title="${esc(a.new_filename || '—')}">${esc(a.new_filename || '—')}</td>
                        <td><span class="badge badge-${badgeFor(a.status)}">${esc(a.status)}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

async function f1Scan() {
    try {
        showToast('Starting F1 scan…');
        const result = await api('/f1/scan', 'POST');
        if (result.status === 'completed') showToast(`F1 scan complete: ${result.moved} moved, ${result.unmatched} unmatched`);
        else showToast(`F1 scan ${result.status}: ${result.reason || ''}`, 'error');
        loadF1Activity();
    } catch (error) {
        showToast(`F1 scan failed: ${error.message}`, 'error');
    }
}

async function f1RefreshCache() {
    const season = document.getElementById('f1-season-select').value || new Date().getFullYear();
    showToast(`Refreshing TheTVDB cache for season ${season}…`);
    try {
        const result = await api(`/f1/refresh-cache?season=${season}`, 'POST');
        if (result.status === 'ok') {
            showToast(`Cached ${result.episodes_cached} episodes for season ${result.season}`);
            loadF1Episodes();
        } else {
            showToast(`Cache refresh failed: ${result.reason}`, 'error');
        }
    } catch (error) {
        showToast(`Cache refresh failed: ${error.message}`, 'error');
    }
}

// ============================================================
// Downloads Cleanup
// ============================================================
async function loadCleanup() {
    await loadCleanupConfig();
    await loadCleanupActivity();
}

async function loadCleanupConfig() {
    const data = await api('/cleanup/config');
    const c = data.config || {};
    document.getElementById('cl-watch-folder').value = c.watch_folder || '';
    document.getElementById('cl-min-age').value = (c.min_age_minutes != null ? c.min_age_minutes : 10);
    document.getElementById('cl-remove-junk').checked = !!c.remove_junk;
    document.getElementById('cl-enabled').checked = !!c.enabled;
    document.getElementById('cl-mode').value = c.schedule_mode || 'interval';
    document.getElementById('cl-interval').value = c.interval_minutes || 30;
    document.getElementById('cl-daily-time').value = c.daily_time || '04:00';
    bindScheduleMode('cl-mode', 'cl-interval-wrap', 'cl-time-wrap');

    try {
        const st = await api('/cleanup/status');
        renderNextRun('cl-next-run', st.next_run, st.job_active, 'Next cleanup');
    } catch (e) { /* ignore */ }
}

async function saveCleanupConfig() {
    const data = {
        watch_folder: document.getElementById('cl-watch-folder').value,
        min_age_minutes: parseInt(document.getElementById('cl-min-age').value) || 0,
        remove_junk: document.getElementById('cl-remove-junk').checked,
        enabled: document.getElementById('cl-enabled').checked,
        schedule_mode: document.getElementById('cl-mode').value,
        interval_minutes: parseInt(document.getElementById('cl-interval').value) || 30,
        daily_time: document.getElementById('cl-daily-time').value || '04:00'
    };
    if (!data.watch_folder) {
        showToast('Watch folder is required', 'error');
        return;
    }
    try {
        await api('/cleanup/config', 'POST', data);
        showToast('Cleanup configuration saved');
        loadCleanupConfig();
        loadDashboardPanel();
    } catch (error) {
        showToast(`Failed to save cleanup config: ${error.message}`, 'error');
    }
}

async function cleanupDryRun() { await runCleanupScan(true); }
async function cleanupRun() {
    if (!confirm('Run cleanup and rename files for real?')) return;
    await runCleanupScan(false);
}

async function runCleanupScan(dryRun) {
    try {
        showToast(dryRun ? 'Running dry run…' : 'Running cleanup…');
        const result = await api(`/cleanup/scan?dry_run=${dryRun}`, 'POST');
        renderCleanupResults(result, dryRun);

        if (result.status === 'completed') {
            showToast(dryRun
                ? `Preview: ${result.renamed} to rename, ${result.extracted || 0} to extract, ${result.junk_removed} junk`
                : `Cleanup done: ${result.renamed} renamed, ${result.extracted || 0} extracted, ${result.junk_removed} junk removed`,
                result.errors > 0 ? 'error' : 'success');
        } else {
            showToast(`Cleanup ${result.status}: ${result.reason || ''}`, 'error');
        }
        if (!dryRun) loadCleanupActivity();
    } catch (error) {
        showToast(`Cleanup failed: ${error.message}`, 'error');
    }
}

function renderCleanupResults(result, dryRun) {
    const card = document.getElementById('cl-results-card');
    const badge = document.getElementById('cl-results-badge');
    const summary = document.getElementById('cl-results-summary');
    const body = document.getElementById('cl-results-body');
    card.classList.remove('is-hidden');

    if (result.status !== 'completed') {
        badge.className = 'tag tag-error';
        badge.textContent = result.status || 'error';
        summary.innerHTML = '';
        body.innerHTML = `<div class="empty-state"><p>${esc(result.reason || 'Scan could not run.')}</p></div>`;
        return;
    }

    badge.className = 'tag ' + (dryRun ? 'tag-preview' : 'tag-applied');
    badge.textContent = dryRun ? 'Preview' : 'Applied';

    const stat = (num, lbl, cls) => `<div class="summary-stat ${cls || ''}"><span class="num">${num}</span><span class="lbl">${lbl}</span></div>`;
    summary.innerHTML =
        stat(result.scanned || 0, 'scanned') +
        stat(result.renamed || 0, 'renamed', 'pos') +
        stat(result.extracted || 0, 'extracted', 'pos') +
        stat(result.junk_removed || 0, 'junk removed') +
        stat(result.skipped || 0, 'skipped') +
        stat(result.errors || 0, 'errors', result.errors > 0 ? 'neg' : '');

    const actions = result.actions || [];
    if (actions.length === 0) {
        body.innerHTML = '<div class="empty-state"><p>Nothing needed attention — no matching files found.</p></div>';
        return;
    }

    const badgeFor = a => (a === 'renamed' || a === 'extracted') ? 'success' : a === 'junk_removed' ? 'warning' : a === 'error' ? 'error' : 'muted';
    body.innerHTML = `
        <table>
            <thead><tr><th>Job folder</th><th>Original</th><th>New name</th><th>Action</th><th>Detail</th></tr></thead>
            <tbody>
                ${actions.map(a => `
                    <tr${dryRun ? ' class="is-preview"' : ''}>
                        <td class="path ellip" title="${esc(a.job_folder)}">${esc(a.job_folder || '—')}</td>
                        <td class="path ellip" title="${esc(a.original)}">${esc(a.original || '—')}</td>
                        <td class="path ellip" title="${esc(a.new)}">${esc(a.new || '—')}</td>
                        <td><span class="badge badge-${badgeFor(a.action)}">${esc(a.action)}</span></td>
                        <td class="log-message">${esc(a.message || '')}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

async function loadCleanupActivity() {
    const filter = document.getElementById('cl-activity-filter').value;
    const params = filter ? `?status=${filter}` : '';
    const data = await api(`/cleanup/activity${params}`);
    const container = document.getElementById('cl-activity-list');

    if (!data.activity || data.activity.length === 0) {
        container.innerHTML = `<div class="empty-state">${icon('inbox')}<p>No activity yet. Run a dry run to preview, then run cleanup.</p></div>`;
        return;
    }

    const badgeFor = a => (a === 'renamed' || a === 'extracted') ? 'success' : a === 'junk_removed' ? 'warning' : a === 'error' ? 'error' : 'muted';
    container.innerHTML = `
        <table>
            <thead><tr><th>Time</th><th>Job folder</th><th>Original</th><th>New name</th><th>Action</th><th></th></tr></thead>
            <tbody>
                ${data.activity.map(a => `
                    <tr>
                        <td class="log-time">${formatDate(a.processed_at)}</td>
                        <td class="path ellip" title="${esc(a.job_folder)}">${esc(a.job_folder || '—')}</td>
                        <td class="path ellip" title="${esc(a.original_name)}">${esc(a.original_name || '—')}</td>
                        <td class="path ellip" title="${esc(a.new_name || '—')}">${esc(a.new_name || '—')}</td>
                        <td><span class="badge badge-${badgeFor(a.action)}">${esc(a.action)}</span></td>
                        <td>${a.dry_run ? '<span class="badge badge-info">preview</span>' : ''}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

// ============================================================
// Init
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(modal.id); });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') document.querySelectorAll('.modal.active').forEach(m => closeModal(m.id));
    });

    // Pre-bind schedule selectors so their fields toggle before their tab loads
    bindScheduleMode('sched-mode', 'sched-interval-wrap', 'sched-time-wrap');
    bindScheduleMode('f1-mode', 'f1-interval-wrap', 'f1-time-wrap');
    bindScheduleMode('cl-mode', 'cl-interval-wrap', 'cl-time-wrap');

    loadDashboard();

    setInterval(() => {
        if (currentTab === 'dashboard') {
            updateNasStatus();
            loadDashboardPanel();
            loadRecentLogs();
        }
    }, 30000);
});
