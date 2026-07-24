# ci-doctor docs site

Static documentation site built with [Astro](https://astro.build).

```sh
cd docs/site
npm install
npm run dev      # local preview at http://localhost:4321
npm run build    # static output in dist/
```

`npm run build` produces a fully static site in `docs/site/dist/` — deploy it
anywhere (GitHub Pages, S3, an internal static host). For a GitHub Pages project
site, set `site` and `base` in `astro.config.mjs` (see the comment there).

Pages live in `src/pages/*.astro`; shared shell in `src/layouts/Base.astro`;
styles in `src/styles/global.css`.
