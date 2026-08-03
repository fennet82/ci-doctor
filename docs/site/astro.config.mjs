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

  // Astro's HTML compressor drops the whitespace either side of a tag when that
  // whitespace contains a newline — so `decides\n<em>where</em>` ships as
  // "decideswhere". Every inline <code>/<em>/<a> the formatter wrapped onto its
  // own line lost its space. Off, the newline survives and the browser collapses
  // it to the single space it is meant to be. Costs ~4K gzipped across the site.
  compressHTML: false,

  // The site was flat before it grew sections. These are the URLs that were
  // published at the top level; each emits a small redirect page so an existing
  // bookmark or inbound link still lands somewhere useful. Delete one only when
  // you are content for it to 404.
  redirects: {
    '/usage': '/reference/cli/',
    '/configuration': '/reference/configuration/',
    '/requirements': '/start/requirements/',
    '/matchers': '/concepts/matchers/',
    '/action': '/cicd/github/',
  },
});
