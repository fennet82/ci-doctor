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
