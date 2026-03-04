// OCDJ Dig — Options Page Script

document.addEventListener('DOMContentLoaded', async () => {
  const urlInput = document.getElementById('backend-url');
  const testBtn = document.getElementById('test-btn');
  const testResult = document.getElementById('test-result');
  const saveBtn = document.getElementById('save-btn');
  const savedMsg = document.getElementById('saved-msg');

  const sites = ['discogs', 'bandcamp', 'youtube', 'soundcloud', 'spotify'];
  const siteToggles = {};
  sites.forEach(s => { siteToggles[s] = document.getElementById(`site-${s}`); });
  const toastToggle = document.getElementById('show-toasts');

  // ── Load Settings ────────────────────────────────────────

  const stored = await chrome.storage.local.get([
    'backendUrl', 'siteSettings', 'showToasts',
  ]);

  urlInput.value = stored.backendUrl || 'http://localhost:8002';

  const siteSettings = stored.siteSettings || {};
  sites.forEach(s => {
    siteToggles[s].checked = siteSettings[s] !== false; // default true
  });
  toastToggle.checked = stored.showToasts !== false;

  // ── Test Connection ──────────────────────────────────────

  testBtn.addEventListener('click', async () => {
    testResult.textContent = 'Testing...';
    testResult.className = '';

    try {
      const resp = await fetch(`${urlInput.value.replace(/\/$/, '')}/api/dig/status/`, {
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        const data = await resp.json();
        testResult.textContent = `Connected! ${data.wanted_count} items in Wanted List`;
        testResult.className = 'success';
      } else {
        testResult.textContent = `Server returned ${resp.status}`;
        testResult.className = 'error';
      }
    } catch (err) {
      testResult.textContent = `Cannot connect: ${err.message}`;
      testResult.className = 'error';
    }
  });

  // ── Save ─────────────────────────────────────────────────

  saveBtn.addEventListener('click', async () => {
    const siteSettings = {};
    sites.forEach(s => { siteSettings[s] = siteToggles[s].checked; });

    await chrome.storage.local.set({
      backendUrl: urlInput.value.replace(/\/$/, '') || 'http://localhost:8002',
      siteSettings,
      showToasts: toastToggle.checked,
    });

    savedMsg.style.display = 'block';
    setTimeout(() => { savedMsg.style.display = 'none'; }, 2000);
  });
});
