// OCDJ Dig — Popup Script

document.addEventListener('DOMContentLoaded', async () => {
  const statusBar = document.getElementById('status-bar');
  const statusText = document.getElementById('status-text');
  const wantedCount = document.getElementById('wanted-count');
  const addForm = document.getElementById('add-form');
  const addBtn = document.getElementById('add-btn');
  const addResult = document.getElementById('add-result');
  const recentList = document.getElementById('recent-list');
  const settingsBtn = document.getElementById('settings-btn');
  const artistInput = document.getElementById('input-artist');
  const titleInput = document.getElementById('input-title');
  const labelInput = document.getElementById('input-label');
  const catnoInput = document.getElementById('input-catno');

  // ── Settings Button ──────────────────────────────────────

  settingsBtn.addEventListener('click', () => {
    chrome.runtime.openOptionsPage();
  });

  // ── Status Check ─────────────────────────────────────────

  try {
    const status = await sendMessage('dig:status');
    if (status && status.healthy) {
      statusBar.className = 'status-bar status-bar--connected';
      statusText.textContent = 'Connected';
      wantedCount.textContent = status.wanted_count ?? '--';
      addBtn.disabled = false;
    } else {
      setDisconnected();
    }
  } catch {
    setDisconnected();
  }

  function setDisconnected() {
    statusBar.className = 'status-bar status-bar--disconnected';
    statusText.textContent = 'Disconnected';
    addBtn.disabled = true;
  }

  // ── Quick Add Form ───────────────────────────────────────

  // Enable submit only when at least artist or title is filled
  function checkFormValid() {
    addBtn.disabled = !artistInput.value.trim() && !titleInput.value.trim();
  }
  artistInput.addEventListener('input', checkFormValid);
  titleInput.addEventListener('input', checkFormValid);

  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    addResult.hidden = true;

    const data = {
      artist: artistInput.value.trim(),
      title: titleInput.value.trim(),
      label: labelInput.value.trim(),
      catalog_number: catnoInput.value.trim(),
      source_site: 'discogs', // default — popup doesn't know the site
      source_url: '',
    };

    if (!data.artist && !data.title) return;

    addBtn.disabled = true;
    addBtn.textContent = 'Adding...';

    try {
      const result = await sendMessage('dig:add', data);
      if (result.duplicate) {
        showResult(`Already in Wanted List (${Math.round(result.fuzzy_score)}% match)`, 'duplicate');
      } else if (result.created) {
        showResult(`Added: ${data.artist}${data.artist && data.title ? ' - ' : ''}${data.title}`, 'success');
        artistInput.value = '';
        titleInput.value = '';
        labelInput.value = '';
        catnoInput.value = '';
        // Refresh count
        const status = await sendMessage('dig:status');
        if (status) wantedCount.textContent = status.wanted_count ?? '--';
        loadRecent();
      }
    } catch (err) {
      showResult(err.message || 'Failed to add', 'error');
    }

    addBtn.disabled = false;
    addBtn.textContent = 'Add to Wanted List';
    checkFormValid();
  });

  function showResult(msg, type) {
    addResult.textContent = msg;
    addResult.className = `result-msg result-msg--${type}`;
    addResult.hidden = false;
    setTimeout(() => { addResult.hidden = true; }, 4000);
  }

  // ── Recent Activity ──────────────────────────────────────

  async function loadRecent() {
    const { recentActivity = [] } = await chrome.storage.local.get('recentActivity');

    if (recentActivity.length === 0) {
      recentList.innerHTML = '<p class="empty-msg">No recent activity</p>';
      return;
    }

    recentList.innerHTML = recentActivity.map((item) => {
      const label = [item.artist, item.title].filter(Boolean).join(' - ') || 'Unknown';
      const ago = timeAgo(item.timestamp);
      return `
        <div class="recent-item">
          <span class="icon">+</span>
          <span class="text" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="time">${ago}</span>
        </div>
      `;
    }).join('');
  }

  loadRecent();

  // ── Helpers ──────────────────────────────────────────────

  function sendMessage(type, data = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type, data }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response && response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response);
        }
      });
    });
  }

  function timeAgo(ts) {
    const seconds = Math.floor((Date.now() - ts) / 1000);
    if (seconds < 60) return 'now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;
    return `${Math.floor(hours / 24)}d`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
});
