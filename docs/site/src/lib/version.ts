// The version the site says it documents, read from the one place that owns it.
//
// pyproject.toml is the source of truth (the release pipeline writes it with
// `uv version`), so nothing here can go stale the way a hardcoded string does.
// Build-time only — the value is baked into the HTML.
//
// Resolved by walking up from the build's working directory, not from
// import.meta.url: the bundler rewrites that, and the first version of this file
// silently returned "" because of it. Everything that builds the site (npm run
// build, mise run docs, the CI job) starts in docs/site, so the walk finds the
// repo root two levels up — and keeps working if that ever moves.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

/** `version = "0.0.1a2"` in the [project] table, first match wins. */
const VERSION = /^\s*version\s*=\s*["']([^"']+)["']/m;

/**
 * The current package version, e.g. "0.0.1a2".
 *
 * Returns an empty string when pyproject.toml cannot be found or parsed — a docs
 * build must not fail over a decoration in the header — but warns, because a
 * missing version is a bug, not a state to render quietly.
 */
export function version(): string {
  let dir = process.cwd();
  for (let up = 0; up < 5; up++) {
    try {
      const found = readFileSync(join(dir, 'pyproject.toml'), 'utf8').match(VERSION)?.[1];
      if (found) return found;
    } catch {
      // Not at this level; keep walking.
    }
    const parent = dirname(dir);
    if (parent === dir) break; // filesystem root
    dir = parent;
  }
  console.warn(`[version] no pyproject.toml with a version found above ${process.cwd()}`);
  return '';
}
