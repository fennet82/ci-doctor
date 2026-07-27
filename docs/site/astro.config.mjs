import { defineConfig } from 'astro/config';

// Project site on GitHub Pages: https://fennet82.github.io/ci-doctor
//
// `base` must match the repo name. It is the one setting that silently breaks
// every internal link when it changes, so nothing hardcodes a leading "/" —
// links go through `url()` and assets through `asset()` in src/lib/url.ts.
export default defineConfig({
  site: 'https://fennet82.github.io',
  base: '/ci-doctor',
  build: { format: 'directory' },
});
