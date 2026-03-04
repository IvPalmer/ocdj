// Injected into w.soundcloud.com widget at document_start in MAIN world.
// Overrides document.referrer to hide chrome-extension:// origin,
// so the SC widget accepts the embedding context.
try {
  Object.defineProperty(document, 'referrer', {
    get: () => 'https://soundcloud.com/',
    configurable: true,
  });
} catch (e) {}
