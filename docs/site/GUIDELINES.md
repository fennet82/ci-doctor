# Documentation site guidelines

How the docs site at <https://fennet82.github.io/ci-doctor> is organised, and the
rules a change to it has to follow. The code equivalent is
[GUIDELINES.md](../../GUIDELINES.md); the *why* behind the architecture is
[PLAN.md](../PLAN.md).

Build and preview commands are in [README.md](README.md).

---

## 1. The shape of the site

Five top-level sections. Every page belongs to exactly one, and the URL says which:

| Section | URL | What belongs here |
|---|---|---|
| Overview | `/` | What the tool is and the one idea it rests on. One page, no children. |
| Get started | `/start/` | The path from nothing to a first verdict. Ordered, read top to bottom. |
| Concepts | `/concepts/` | What the moving parts are and why they behave that way. Explains, does not enumerate. |
| CI setup | `/cicd/` | Drop-in configuration for one pipeline. One page per provider. |
| Reference | `/reference/` | Exhaustive lookups: every command, flag and config key. |

```
/                             Overview
/start/                       index
  /start/install/
  /start/first-run/
  /start/requirements/
/concepts/                    index — the one rule, both architecture diagrams, the invariants
  /concepts/pipeline/
  /concepts/attribution/
  /concepts/evidence/
  /concepts/matchers/
  /concepts/code-map/
/cicd/                        index — pick your provider
  /cicd/gitlab/
  /cicd/github/
  /cicd/local/
/reference/                   index
  /reference/cli/
  /reference/configuration/
```

### Choosing where a new page goes

Ask what the reader is doing, not what the page is about:

- Trying to **get it running for the first time** → `/start/`.
- Trying to **understand why it behaves that way** → `/concepts/`.
- Trying to **paste config into their pipeline** → `/cicd/`.
- Trying to **look up a flag or key they already know exists** → `/reference/`.

If a page fits two sections, it is usually two pages: the explanation belongs in
Concepts and the table belongs in Reference, cross-linked. Duplicating prose in
both is how the two drift apart.

**Do not add a sixth top-level section** without a strong reason. Five is already
the point at which a navbar starts being scanned rather than read.

---

## 2. Adding a page

Two steps, in this order:

1. **Add the entry to `src/lib/nav.ts`.** That file is the single source of truth
   for the sidebar and the cards on each section index. A page that is not in
   `nav.ts` is unreachable except by direct URL.
2. **Create `src/pages/<section>/<page>.astro`.** The directory must match the
   section's `href` so the URL and the sidebar agree.

Nothing else is wired by hand — `Base.astro` renders the sidebar on every page,
and the sidebar highlights whichever entry matches the current URL.

A `blurb` is required in `nav.ts`. It is one sentence, shown on the section index
card and as the sidebar link's tooltip. Write what the reader will *find*, not
what the page is titled.

### Navigation is the sidebar, and only the sidebar

Every section is in the sidebar on every page. Sections are `<details>` elements:
closed by default, and the one containing the current page is open — so the
highlighted page is never hidden behind a collapsed section.

A section head has **two targets on one row**: the chevron toggles it open, the
label navigates to the section's index page. A label that only toggled left that
index page with no way in. If a section ever has no index page of its own, set
`hasIndex: false` in `nav.ts` and the label points at the first page underneath
instead — never leave a label pointing at a URL that does not build.

So do **not** add a second navigation layer: no section tabs, no breadcrumb rail,
no in-page table of contents built from headings. If a page is long enough that
it feels like it needs its own contents list, that is the signal to split it into
two sidebar entries instead.

### Moving or renaming a page

Add the old path to `redirects` in `astro.config.mjs`. The site is published, so a
moved URL is a broken inbound link until you do. Astro emits a small redirect page
for each entry — they cost nothing.

---

## 3. Links and assets

The site is served from `/ci-doctor`, **not** `/`. Astro does not rewrite plain
`href`/`src` attributes, so a hardcoded internal link works on `astro dev` and
404s in production — the exact kind of break nobody notices until it ships.

```astro
import { asset, url } from '../../lib/url';

<a href={url('/concepts/matchers/')}>Matchers</a>   <!-- page -->
<img src={asset('/ci-doctor-icon.svg')} />          <!-- file in public/ -->
```

Every internal link goes through `url()`. Every asset goes through `asset()`. No
exceptions. Pages nested one directory deep import from `../../lib/url`.

---

## 4. Generated content must not be hand-written

Anything that exists in `ci_doctor/config/defaults.yml` is generated into
`src/data/*.json` by `mise run docs:data`, and a test fails when the committed
JSON drifts from the config:

| File | Source | Used by |
|---|---|---|
| `src/data/matchers.json` | the 35 shipped matcher packs | `/concepts/matchers/` |
| `src/data/phases.json` | the section-name → phase map | `/concepts/attribution/` |

**Never edit those JSON files.** A hand-written table is stale by the next commit,
which is the whole reason they are generated.

The one thing that *cannot* be derived from config is what a matcher pack is
**for**, so that prose lives in `NOTES` in `scripts/gen_docs_data.py`, keyed by
matcher id. Add a pack without a note and the generator refuses to run — a blank
cell on the site is not an acceptable outcome.

Regenerate after any change to `defaults.yml`:

```sh
mise run docs:data
```

---

## 5. Diagrams

Use `src/components/Mermaid.astro`:

```astro
<Mermaid code={source} label="…what the diagram shows, in a sentence…" caption="…" />
```

- `label` is **required** — the rendered SVG is a single image to a screen reader,
  and the label is the only thing it gets.
- Colours come from the site's CSS tokens at render time, so a diagram follows the
  theme toggle. Never hardcode a colour in mermaid source.
- Diagrams render at their **natural size** (`useMaxWidth: false`) and the
  container scrolls sideways when one is wider than the column. Do not "fix" that
  by letting mermaid scale the SVG to fit: scaling shrinks the labels with it,
  and raising `fontSize` then just produces a bigger SVG that gets scaled down
  further. Illegible type is worse than a scrollbar.
- Mermaid is client-rendered and is a large bundle. Astro code-splits it, so only
  the pages that use a diagram pay for it — keep it that way by not putting a
  diagram on a page that does not need one.
- A syntax error shows up as an empty box in the browser, not a build failure.
  Check any diagram you add by loading the page.

**Do not reach for a diagram by default.** The layered architecture view on
`/concepts/` is hand-written CSS precisely because it needs no JavaScript. A
diagram earns its place when it shows *flow or branching* that prose cannot.

---

## 6. Writing style

The site is written to be read once, in order, by someone debugging a red
pipeline. Match what is already there:

- **Say what it decides, not what it is.** "Decides which phase failed" beats "the
  attribution module".
- **Lead with the consequence.** A reader skimming for the answer should hit it in
  the first sentence of the paragraph.
- **Name the trade-off when there is one.** `npm ERR!` ranks below `tsc` *because*
  it trails the error that caused it. The reason is the useful half.
- **No marketing voice, no exclamation marks, no "simply" or "just".**
- Use `<div class="note">` for a caveat worth stopping at. Use it sparingly; three
  notes on a page means none of them is read.
- Prefer a table over a bulleted list when every item has the same shape.
- Code samples are `<Code code={…} lang="…" theme="github-dark" />` from
  `astro:components`, with the source in the frontmatter rather than inline.

Never document behaviour you have not checked in the code. The site is read as
authoritative, and a wrong config key costs a reader more than a missing one.

---

## 7. Styling

- Design tokens (`--bg`, `--fg`, `--accent`, `--border`, `--mono`, …) live in
  `src/styles/global.css`. **Use the tokens** — never a raw hex value in a page,
  or dark mode silently breaks.
- **The chrome is full-bleed; only the prose is capped.** The header, sidebar and
  footer span the viewport with a `--gutter` inset. The page is a two-column grid
  (`.shell`) — sidebar at the left edge, content taking the rest — and `main`
  is the only thing with a `max-width` (`--maxw`), centred in whatever room the
  sidebar leaves. Do not put a `max-width` back on the chrome: that is what left
  dead space down both sides of a wide screen.
- `--sidebar-w`, `--maxw`, `--gutter` and `--header-h` (what the sticky sidebar
  sticks under) are the layout tokens. Change one and check the others.
- Sidebar state: the header's panel button collapses it on desktop
  (`<html data-sidebar="collapsed">`, persisted in `localStorage`, restored
  before first paint) and opens it as a drawer below 1000px. Both come from the
  same button; do not add a second control.
- Scrollbars are styled globally in `global.css` (`scrollbar-width: thin` plus
  the `::-webkit-scrollbar` rules) and follow the theme tokens. Do not restyle
  them per component.
- Shared element styles (`table`, `.note`, `.grid`, `.card`, `.tight`, `.lead`,
  `.snippet`) also live in `global.css`. Reach for those before writing new CSS.
- Page-specific CSS goes in that page's scoped `<style>` block. If two pages need
  the same block, it belongs in a component or in `global.css` — not copied.
- Everything must work in light and dark, and at 360px wide. Wide content
  (tables, diagrams) scrolls inside its own container; the page body never
  scrolls sideways.

---

## 8. Before you push

```sh
mise run docs:data     # if defaults.yml changed
mise run test          # the drift tests live in tests/test_docs_data.py
mise run docs:build    # npm ci && npm run build — same as CI
```

`.github/workflows/docs.yml` deploys on every push to `master` that touches
`docs/site/**`. Pull requests only *build* the site, so a branch can never
overwrite what is live.
