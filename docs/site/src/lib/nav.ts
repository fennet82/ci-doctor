// The site's information architecture, in one place.
//
// The header nav, every section's sub-nav, and the cards on each section index
// are all derived from this — so adding a page means adding one entry here and
// one .astro file, and nothing can end up unreachable from the nav.
//
// `href` values are site-relative and always start and end with "/". They go
// through url() before they reach the DOM (see url.ts and the base gotcha).

export interface NavPage {
  href: string;
  label: string;
  /** One line, shown on the section index card and in the sub-nav tooltip. */
  blurb: string;
}

export interface NavSection extends NavPage {
  pages: NavPage[];
  /**
   * Whether `href` resolves to a real page (`src/pages/<section>/index.astro`).
   * Defaults to true. Set it false for a section that is only a grouping — the
   * sidebar then points its label at the first page underneath instead of at a
   * URL that 404s.
   */
  hasIndex?: boolean;
}

export const nav: NavSection[] = [
  {
    href: '/',
    label: 'Overview',
    blurb: 'What ci-doctor is and the one idea it is built on.',
    pages: [],
  },
  {
    href: '/start/',
    label: 'Get started',
    blurb: 'Install it, run it on a real failure, and check what it needs.',
    pages: [
      { href: '/start/install/', label: 'Install', blurb: 'uv, pip, the Docker image, or no install at all on GitHub Actions.' },
      { href: '/start/first-run/', label: 'First run', blurb: 'Replay a saved log offline, then point it at a live pipeline. What the report looks like.' },
      { href: '/start/requirements/', label: 'Requirements', blurb: 'Python or the image, a read-scoped token, and what it does on an air-gapped network.' },
    ],
  },
  {
    href: '/concepts/',
    label: 'Concepts',
    blurb: 'How it decides where a job failed, and what each part of the code does.',
    pages: [
      { href: '/concepts/pipeline/', label: 'The pipeline', blurb: 'Ten stages, one pass, no loops — and the config that tunes each one.' },
      { href: '/concepts/attribution/', label: 'Sections & attribution', blurb: 'Sections, phases, the shipped phase map, and the rule ladder that picks the blame.' },
      { href: '/concepts/evidence/', label: 'Evidence', blurb: 'Denoising, matcher windows, the token budget, redaction, and the bundle the report sees.' },
      { href: '/concepts/matchers/', label: 'Matchers', blurb: 'All 35 shipped packs — what each one catches, and how to add or retune your own.' },
      { href: '/concepts/code-map/', label: 'Code map', blurb: 'Package by package: what every file decides, and the function to open first.' },
    ],
  },
  {
    href: '/cicd/',
    label: 'CI setup',
    blurb: 'Drop-in configuration for the pipeline you actually run.',
    pages: [
      { href: '/cicd/gitlab/', label: 'GitLab CI', blurb: 'The .gitlab-ci.yml job, MR notes, an LLM endpoint, and self-hosted instances.' },
      { href: '/cicd/github/', label: 'GitHub Actions', blurb: 'The workflow, plus the full action reference: inputs, outputs, PR comments, Enterprise.' },
      { href: '/cicd/local/', label: 'Local & air-gapped', blurb: 'Run it from your laptop, ship it as an image, or install from an offline wheel bundle.' },
    ],
  },
  {
    href: '/reference/',
    label: 'Reference',
    blurb: 'Every command, flag and config key, with its default.',
    pages: [
      { href: '/reference/cli/', label: 'CLI', blurb: 'analyze and config: arguments, flags, deterministic mode, and --verbose output.' },
      { href: '/reference/configuration/', label: 'Configuration', blurb: 'How the five layers combine, the env-var form, LLM backends, and every key.' },
    ],
  },
];
