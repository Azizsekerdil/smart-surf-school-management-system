/**
 * Copy third-party front-end assets from node_modules into static/vendor/.
 *
 * The application must run with no internet access, so nothing is loaded from a
 * CDN. Everything the browser needs is committed under static/vendor/ and
 * served by WhiteNoise.
 *
 * Run:  npm run vendor
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const VENDOR = path.join(ROOT, 'static', 'vendor');

/** Files copied verbatim: [source relative to node_modules, destination relative to static/vendor]. */
const FILES = [
  ['htmx.org/dist/htmx.min.js', 'htmx/htmx.min.js'],
  ['htmx.org/dist/ext/sse.js', 'htmx/ext/sse.js'],
  ['htmx.org/LICENSE', 'htmx/LICENSE'],
  ['alpinejs/dist/cdn.min.js', 'alpine/alpine.min.js'],
  // Alpine ships its licence text inside package.json ("license": "MIT");
  // recorded in docs/OPEN_SOURCE_LICENSES.md instead of a copied file.
  ['chart.js/dist/chart.umd.js', 'chartjs/chart.umd.js'],
  ['chart.js/LICENSE.md', 'chartjs/LICENSE.md'],
  ['lucide-static/LICENSE', 'icons/LICENSE'],
];

/**
 * Icons used by the navigation, dashboards and module screens.
 * Only these are vendored — copying all ~1500 Lucide icons would bloat the repo.
 */
const ICONS = [
  // navigation
  'layout-dashboard', 'graduation-cap', 'users', 'user-check', 'book-open',
  'calendar-days', 'tent', 'package', 'arrow-left-right', 'wrench', 'waves',
  'map-pin', 'shield-alert', 'heart-handshake', 'wallet', 'shopping-cart',
  'file-text', 'chart-line', 'sparkles', 'cpu', 'terminal', 'gauge',
  'database-backup', 'scroll-text', 'school', 'circle-help', 'shield-check',
  'settings',
  // actions
  'plus', 'pencil', 'trash-2', 'search', 'filter', 'download', 'upload',
  'refresh-cw', 'check', 'x', 'chevron-down', 'chevron-up', 'chevron-left',
  'chevron-right', 'chevrons-left', 'chevrons-right', 'ellipsis',
  'ellipsis-vertical', 'external-link', 'copy', 'printer', 'save', 'send',
  'play', 'square', 'rotate-ccw', 'log-out', 'menu', 'eye', 'eye-off',
  'lock', 'lock-open',
  // status & feedback
  'circle-check', 'circle-alert', 'triangle-alert', 'info', 'circle-x',
  'clock', 'calendar', 'bell', 'bell-ring', 'flag', 'star', 'heart',
  'trending-up', 'trending-down', 'minus', 'activity', 'loader-circle',
  // domain
  'sun', 'cloud', 'cloud-rain', 'wind', 'thermometer', 'droplets', 'sunrise',
  'sunset', 'umbrella', 'life-buoy', 'anchor', 'compass', 'ruler', 'weight',
  'qr-code', 'barcode', 'camera', 'image', 'paperclip', 'credit-card',
  'banknote', 'receipt', 'percent', 'hash', 'phone', 'mail', 'globe',
  'languages', 'moon', 'user', 'user-plus', 'user-round', 'building-2',
  'clipboard-list', 'clipboard-check', 'list-checks', 'bookmark', 'tag',
  'folder', 'file', 'file-spreadsheet', 'database', 'server', 'hard-drive',
  'zap', 'brain', 'message-square', 'bot', 'key-round', 'history',
];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

/**
 * Strip a trailing `//# sourceMappingURL=` comment.
 *
 * The .map files are not vendored -- they are large, they are only useful to
 * someone debugging the library itself, and shipping them would publish the
 * dependency's original sources. Leaving the *reference* behind, however, is
 * not harmless: WhiteNoise's CompressedManifestStaticFilesStorage resolves
 * every asset reference during `collectstatic` and aborts with
 * MissingFileError when one cannot be found, which breaks the documented
 * production deployment at its first step.
 */
function stripSourceMapRef(text) {
  return text.replace(/^\s*\/\/[#@]\s*sourceMappingURL=.*$/gm, '').replace(/\s+$/, '
');
}

const TEXT_EXTENSIONS = new Set(['.js', '.css', '.mjs']);

function copy(from, to) {
  const source = path.join(ROOT, 'node_modules', from);
  const destination = path.join(VENDOR, to);
  if (!fs.existsSync(source)) {
    console.warn(`  SKIP (missing): ${from}`);
    return false;
  }
  ensureDir(path.dirname(destination));
  if (TEXT_EXTENSIONS.has(path.extname(destination))) {
    fs.writeFileSync(destination, stripSourceMapRef(fs.readFileSync(source, 'utf8')), 'utf8');
  } else {
    fs.copyFileSync(source, destination);
  }
  return true;
}

function main() {
  console.log('Vendoring front-end assets into static/vendor/ ...');
  ensureDir(VENDOR);

  let copied = 0;
  for (const [from, to] of FILES) {
    if (copy(from, to)) {
      console.log(`  ok  ${to}`);
      copied += 1;
    }
  }

  // Icons -> one sprite-free directory of raw SVGs, read by the {% icon %} tag.
  const iconSource = path.join(ROOT, 'node_modules', 'lucide-static', 'icons');
  const iconDestination = path.join(VENDOR, 'icons');
  ensureDir(iconDestination);

  let iconCount = 0;
  const missing = [];
  for (const name of ICONS) {
    const file = path.join(iconSource, `${name}.svg`);
    if (!fs.existsSync(file)) {
      missing.push(name);
      continue;
    }
    fs.copyFileSync(file, path.join(iconDestination, `${name}.svg`));
    iconCount += 1;
  }

  console.log(`  ok  ${iconCount} icons`);
  if (missing.length) {
    console.warn(`  WARNING: ${missing.length} icon(s) not found in lucide-static: ${missing.join(', ')}`);
  }
  console.log(`Done. ${copied} files + ${iconCount} icons vendored.`);

  if (missing.length) process.exitCode = 0; // missing icons degrade to a placeholder, not a build failure
}

main();
