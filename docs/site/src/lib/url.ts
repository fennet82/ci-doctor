// GitHub Pages serves this site from /ci-doctor, not /. Astro rewrites nothing
// inside plain `href`/`src` attributes, so every internal link and asset goes
// through here — otherwise the site works on `astro dev` and 404s in production.

const BASE = import.meta.env.BASE_URL.replace(/\/+$/, '');

/** Link to a page. Keeps the trailing slash `build.format: 'directory'` wants. */
export function url(path: string): string {
  const clean = path.replace(/^\/+/, '').replace(/\/+$/, '');
  return clean ? `${BASE}/${clean}/` : `${BASE}/`;
}

/** Link to a file in public/. No trailing slash — it is a file, not a directory. */
export function asset(path: string): string {
  return `${BASE}/${path.replace(/^\/+/, '')}`;
}
