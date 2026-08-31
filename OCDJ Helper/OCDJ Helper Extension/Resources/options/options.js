// OCDJ Standalone — Options Page Script

document.addEventListener('DOMContentLoaded', async () => {
  const discogsTokenInput = document.getElementById('discogs-token');
  const saveBtn = document.getElementById('save-btn');
  const savedMsg = document.getElementById('saved-msg');

  const sites = ['discogs', 'bandcamp', 'youtube', 'soundcloud', 'spotify'];
  const siteToggles = {};
  sites.forEach(s => { siteToggles[s] = document.getElementById(`site-${s}`); });
  const toastToggle = document.getElementById('show-toasts');
  const digTokenInput = document.getElementById('dig-token');

  // ── Load Settings ────────────────────────────────────────

  const stored = await chrome.storage.local.get([
    'discogsToken', 'siteSettings', 'showToasts', 'digToken',
  ]);

  discogsTokenInput.value = stored.discogsToken || '';
  digTokenInput.value = stored.digToken || '';

  const siteSettings = stored.siteSettings || {};
  sites.forEach(s => {
    siteToggles[s].checked = siteSettings[s] !== false;
  });
  toastToggle.checked = stored.showToasts !== false;

  // ── Save ─────────────────────────────────────────────────

  saveBtn.addEventListener('click', async () => {
    const siteSettings = {};
    sites.forEach(s => { siteSettings[s] = siteToggles[s].checked; });

    await chrome.storage.local.set({
      discogsToken: discogsTokenInput.value.trim(),
      digToken: digTokenInput.value.trim(),
      siteSettings,
      showToasts: toastToggle.checked,
    });

    savedMsg.style.display = 'block';
    setTimeout(() => { savedMsg.style.display = 'none'; }, 2000);
  });
});
