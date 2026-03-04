// OCDJ Standalone — Popup (Wantlist Viewer + Export)

document.addEventListener('DOMContentLoaded', async () => {
  const searchInput = document.getElementById('search-input');
  const wantlistEl = document.getElementById('wantlist');
  const itemCount = document.getElementById('item-count');
  const exportBtn = document.getElementById('export-btn');
  const clearBtn = document.getElementById('clear-btn');
  const settingsBtn = document.getElementById('settings-btn');

  let allItems = [];

  // ── Settings ───────────────────────────────────────────────

  settingsBtn.addEventListener('click', () => {
    chrome.runtime.openOptionsPage();
  });

  // ── Load Wantlist ──────────────────────────────────────────

  async function loadWantlist() {
    const { wantlist = [] } = await chrome.storage.local.get('wantlist');
    allItems = wantlist.sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
    renderList();
  }

  // ── Search / Filter ────────────────────────────────────────

  searchInput.addEventListener('input', () => renderList());

  function renderList() {
    const query = searchInput.value.toLowerCase().trim();

    const filtered = query
      ? allItems.filter(item => {
          const searchable = [
            item.artist, item.title, item.release_name, item.label, item.source_site,
          ].filter(Boolean).join(' ').toLowerCase();
          return searchable.includes(query);
        })
      : allItems;

    itemCount.textContent = `${allItems.length} item${allItems.length !== 1 ? 's' : ''}`;

    if (filtered.length === 0) {
      wantlistEl.innerHTML = allItems.length === 0
        ? '<p class="empty-msg">Wantlist is empty. Click Wantlist on any platform to start.</p>'
        : '<p class="empty-msg">No matches found.</p>';
      return;
    }

    wantlistEl.innerHTML = filtered.map((item, i) => {
      const artist = escapeHtml(item.artist || '');
      const title = escapeHtml(item.title || item.release_name || '');
      const displayTitle = artist && title
        ? `${artist} - ${title}`
        : (artist || title || 'Unknown');

      const details = [];
      if (item.release_name && item.title) details.push(escapeHtml(item.release_name));
      if (item.label) details.push(escapeHtml(item.label));
      if (item.catalog_number) details.push(escapeHtml(item.catalog_number));

      const site = item.source_site || '';
      const sourceUrl = item.source_url || '';

      return `
        <div class="wantlist-item" data-index="${i}">
          <div class="wantlist-item-main">
            <span class="wantlist-item-title">${displayTitle}</span>
            <button class="wantlist-item-remove" data-idx="${allItems.indexOf(item)}" title="Remove">&times;</button>
          </div>
          <div class="wantlist-item-meta">
            ${details.length ? details.join(' &middot; ') + ' &middot; ' : ''}${site ? `<span class="platform-badge ${site}">${site}</span>` : ''}${sourceUrl ? ` <a href="${escapeHtml(sourceUrl)}" target="_blank" class="source-link" title="${escapeHtml(sourceUrl)}">link</a>` : ''}
          </div>
        </div>
      `;
    }).join('');

    // Remove buttons
    wantlistEl.querySelectorAll('.wantlist-item-remove').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.idx, 10);
        allItems.splice(idx, 1);
        await chrome.storage.local.set({ wantlist: allItems });
        renderList();
      });
    });
  }

  // ── Export ─────────────────────────────────────────────────

  exportBtn.addEventListener('click', () => {
    if (allItems.length === 0) return;

    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10);
    let text = `OCDJ Wantlist — Exported ${dateStr}\n`;
    text += '========================================\n\n';

    allItems.forEach(item => {
      const artist = item.artist || '';
      const title = item.title || '';
      const release = item.release_name || '';
      const label = item.label || '';
      const catno = item.catalog_number || '';

      // Main line
      const mainLine = artist && title
        ? `${artist} - ${title}`
        : artist && release
          ? `${artist} - ${release}`
          : (artist || title || release || 'Unknown');
      text += `${mainLine}\n`;

      // Details line
      const details = [];
      if (release && title) details.push(`Release: ${release}`);
      if (label) details.push(`Label: ${label}`);
      if (catno) details.push(`Cat#: ${catno}`);
      if (details.length) text += `  ${details.join(' | ')}\n`;

      if (item.source_url) text += `  Source: ${item.source_url}\n`;
      text += '\n';
    });

    // Download as file
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ocdj-wantlist-${dateStr}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  });

  // ── Clear ──────────────────────────────────────────────────

  clearBtn.addEventListener('click', async () => {
    if (allItems.length === 0) return;
    if (!confirm(`Clear all ${allItems.length} items from wantlist?`)) return;

    allItems = [];
    await chrome.storage.local.set({ wantlist: [] });
    renderList();
  });

  // ── Helpers ────────────────────────────────────────────────

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Init ───────────────────────────────────────────────────

  loadWantlist();
});
