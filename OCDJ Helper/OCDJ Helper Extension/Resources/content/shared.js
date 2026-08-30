// OCDJ Standalone — Shared Content Script Utilities
// Loaded before every site-specific script

const OCDJ = (() => {
  const INJECTED_ATTR = 'data-ocdj-injected';

  // ── Button Factory ───────────────────────────────────────

  function createDigButton({ onClick, tooltip = 'Add to Wantlist', size = 'small', label }) {
    if (!label) label = 'Wantlist';
    const btn = document.createElement('button');
    btn.className = `ocdj-dig-btn ocdj-dig-btn--${size}`;
    btn.setAttribute(INJECTED_ATTR, 'true');
    btn.setAttribute('title', tooltip);
    btn.textContent = label;

    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (btn.classList.contains('ocdj-dig-btn--added') || btn.classList.contains('ocdj-dig-btn--loading')) return;

      btn.classList.add('ocdj-dig-btn--loading');
      btn.textContent = '...';

      try {
        const result = await onClick();
        if (result && result.duplicate) {
          markAsDuplicate(btn);
          showToast('Already in Wantlist', 'duplicate');
        } else if (result && result.created) {
          markAsAdded(btn);
          const artist = result.item?.artist || '';
          const title = result.item?.title || result.item?.release_name || '';
          if (result.offline) {
            // The app was unreachable and this went to browser storage instead.
            // Saying "Saved" would claim it reached the Wanted List; it didn't.
            showToast('Saved locally — app unreachable, not in Wanted List', 'duplicate');
          } else {
            showToast(`Saved: ${artist}${artist && title ? ' - ' : ''}${title}`, 'success');
          }
        } else if (result && result.error) {
          btn.classList.remove('ocdj-dig-btn--loading');
          btn.textContent = label;
          showToast(result.error, 'error');
        } else {
          // Any shape we don't recognise. Without this the button keeps its
          // loading state forever and the click looks like it broke the page —
          // which is exactly what a dropped round trip to the service worker
          // looked like in Safari, where MV3 workers are torn down eagerly.
          btn.classList.remove('ocdj-dig-btn--loading');
          btn.textContent = label;
          showToast('No answer from the extension — try again', 'error');
        }
      } catch (err) {
        btn.classList.remove('ocdj-dig-btn--loading');
        btn.textContent = label;
        showToast(err.message || 'Failed to add', 'error');
      }
    });

    return btn;
  }

  function markAsAdded(btn) {
    btn.classList.remove('ocdj-dig-btn--loading');
    btn.classList.add('ocdj-dig-btn--added');
    btn.textContent = '\u2713';
    btn.title = 'Saved to Wantlist';
  }

  function markAsDuplicate(btn) {
    btn.classList.remove('ocdj-dig-btn--loading');
    btn.classList.add('ocdj-dig-btn--duplicate');
    btn.textContent = '\u2713';
    btn.title = 'Already in Wantlist';
  }

  // ── Play/Queue Button Factory ─────────────────────────────

  function createPlayButton({ onClick, tooltip = 'Queue for listening', size = 'small', label }) {
    if (!label) label = 'Queue';
    const originalLabel = label;
    const btn = document.createElement('button');
    btn.className = `ocdj-play-btn ocdj-play-btn--${size}`;
    btn.setAttribute(INJECTED_ATTR, 'true');
    btn.setAttribute('title', tooltip);
    btn.textContent = label;

    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (btn.classList.contains('ocdj-play-btn--queued') || btn.classList.contains('ocdj-play-btn--loading')) return;

      btn.classList.add('ocdj-play-btn--loading');
      btn.textContent = '...';

      try {
        const result = await onClick();
        if (result && !result.error) {
          btn.classList.remove('ocdj-play-btn--loading');
          btn.classList.add('ocdj-play-btn--queued');
          btn.textContent = '\u2713';
          btn.title = 'Queued for listening';
          showToast('Queued for listening', 'success');
        } else {
          btn.classList.remove('ocdj-play-btn--loading');
          btn.textContent = originalLabel;
          showToast(result?.error || 'Failed to queue', 'error');
        }
      } catch (err) {
        btn.classList.remove('ocdj-play-btn--loading');
        btn.textContent = originalLabel;
        showToast(err.message || 'Failed to queue', 'error');
      }
    });

    return btn;
  }

  // ── Button Group (inline container to prevent stretching) ──

  function createButtonGroup(...buttons) {
    const group = document.createElement('span');
    group.className = 'ocdj-btn-group';
    group.setAttribute(INJECTED_ATTR, 'true');
    buttons.forEach(b => group.appendChild(b));
    return group;
  }

  // ── Toast Notifications ──────────────────────────────────

  let toastContainer = null;

  function getToastContainer() {
    if (toastContainer && document.body.contains(toastContainer)) return toastContainer;
    toastContainer = document.createElement('div');
    toastContainer.className = 'ocdj-toast-container';
    toastContainer.setAttribute(INJECTED_ATTR, 'true');
    document.body.appendChild(toastContainer);
    return toastContainer;
  }

  function showToast(message, type = 'info') {
    const container = getToastContainer();
    const toast = document.createElement('div');
    toast.className = `ocdj-toast ocdj-toast--${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add('ocdj-toast--visible'));

    setTimeout(() => {
      toast.classList.remove('ocdj-toast--visible');
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  // ── Message Helpers ──────────────────────────────────────

  function sendToBackground(type, data = {}) {
    return new Promise((resolve, reject) => {
      if (!chrome?.runtime?.sendMessage) {
        showToast('Extension updated — refresh this page', 'error');
        return reject(new Error('Extension updated — refresh this page'));
      }
      try {
        chrome.runtime.sendMessage({ type, data }, (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message || 'Extension error'));
          } else if (response && response.error) {
            reject(new Error(response.error));
          } else if (response === undefined) {
            // Safari can tear the service worker down mid-round-trip and hand
            // back nothing without setting lastError. Treat a missing answer as
            // a failure rather than resolving undefined into the caller.
            reject(new Error('No answer from the extension — try again'));
          } else {
            resolve(response);
          }
        });
      } catch (err) {
        showToast('Extension updated — refresh this page', 'error');
        reject(err);
      }
    });
  }

  // ── DOM Observer ─────────────────────────────────────────

  function observeDOM(callback, rootSelector = 'body') {
    const root = document.querySelector(rootSelector) || document.body;
    const observer = new MutationObserver((mutations) => {
      if (observer._debounceTimer) clearTimeout(observer._debounceTimer);
      observer._debounceTimer = setTimeout(() => callback(mutations), 250);
    });
    observer.observe(root, { childList: true, subtree: true });
    return observer;
  }

  // ── Already Injected Check ───────────────────────────────

  function isInjected(el) {
    if (!el) return true;
    return !!el.querySelector(`[${INJECTED_ATTR}]`);
  }

  // ── Video Title Parser (JS port of backend parsers.py) ──

  function parseVideoTitle(rawTitle) {
    if (!rawTitle) return { artist: '', title: '', raw_title: rawTitle || '' };

    let cleaned = rawTitle.trim();

    const suffixPatterns = [
      /\s*[(\[]\s*(?:official\s+)?(?:video|audio|music\s+video|lyric\s+video|visualizer|clip)\s*[)\]]/gi,
      /\s*[(\[]\s*(?:HQ|HD|4K|1080p|720p|lyrics?)\s*[)\]]/gi,
      /\s*[(\[]\s*(?:full\s+)?(?:album|EP)\s*[)\]]/gi,
      /\s*[(\[]\s*(?:original\s+mix|extended\s+mix|remix)\s*[)\]]/gi,
      /\s*[(\[]\s*(?:out\s+now|free\s+download|premiere)\s*[)\]]/gi,
    ];
    for (const pattern of suffixPatterns) {
      cleaned = cleaned.replace(pattern, '');
    }

    cleaned = cleaned.replace(/^\d{1,3}\s*[.)\-]\s*/, '');

    const separators = [' - ', ' -- ', ' \u2014 ', ' | ', ' // '];
    for (const sep of separators) {
      if (cleaned.includes(sep)) {
        const parts = cleaned.split(sep);
        const artist = parts[0].trim();
        const title = parts.slice(1).join(sep).trim();
        if (artist && title) {
          return { artist, title, raw_title: rawTitle };
        }
      }
    }

    return { artist: '', title: cleaned.trim(), raw_title: rawTitle };
  }

  // ── Public API ───────────────────────────────────────────

  return {
    INJECTED_ATTR,
    createDigButton,
    createPlayButton,
    createButtonGroup,
    markAsAdded,
    markAsDuplicate,
    showToast,
    sendToBackground,
    observeDOM,
    isInjected,
    parseVideoTitle,
  };
})();
