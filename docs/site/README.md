# ci-doctor docs site

Static documentation site built with [Astro](https://astro.build), published to
GitHub Pages at <https://fennet82.github.io/ci-doctor>.

```sh
cd docs/site
npm install
npm run dev      # local preview at http://localhost:4321/ci-doctor
npm run build    # static output in dist/
```

## Deployment

`.github/workflows/docs.yml` builds and deploys on every push to `master` that
touches `docs/site/**`. Pull requests only *build* the site (the `docs` job in
`ci.yml`), so a branch can never overwrite what is live.

Enable it once in **Settings → Pages → Source: GitHub Actions**.

## The `base` gotcha

The site is served from `/ci-doctor`, not `/`. Astro rewrites nothing inside
plain `href`/`src` attributes, so **every internal link goes through
`src/lib/url.ts`**:

```astro
import { asset, url } from '../lib/url';
<a href={url('/usage/')}>Usage</a>          <!-- page -->
<img src={asset('/ci-doctor-icon.svg')} />  <!-- file -->
```

A hardcoded `href="/usage/"` works on `astro dev` and 404s in production, which
is exactly the kind of break nobody notices until it ships.

## Layout

Pages live in `src/pages/*.astro`; shared shell in `src/layouts/Base.astro`;
design tokens and components in `src/styles/global.css`.
