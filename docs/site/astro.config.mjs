import { defineConfig } from 'astro/config';

// Static site. If you host on GitHub Pages as a project site
// (https://<user>.github.io/ci-doctor), also set:
//   site: 'https://<user>.github.io', base: '/ci-doctor',
export default defineConfig({
  build: { format: 'directory' },
});
