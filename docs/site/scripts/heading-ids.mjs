// Give every heading in the built site a slug id.
//
// Starlight gets these for free: its pages are Markdown, and Astro's markdown
// pipeline slugs headings on the way through. Ours are hand-written .astro, where
// nothing does — and without ids Pagefind has no anchors to record, so a search
// result can only ever link to the top of a page, and sub-results (the matching
// sections under each page) never appear at all.
//
// Runs between `astro build` and `pagefind`, so the ids are in the HTML that gets
// indexed *and* in the HTML that gets served — the anchor has to exist in both for
// the link to land anywhere.

import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const DIST = new URL('../dist/', import.meta.url).pathname;

/** `<h2 ...>text</h2>` — h2 through h4, the levels the sidebar and search care about. */
const HEADING = /<(h[234])([^>]*)>([\s\S]*?)<\/\1>/g;

/** GitHub-style: strip markup, lowercase, non-alphanumerics to hyphens. */
const slugify = (html) =>
  html
    .replace(/<[^>]+>/g, '')
    .replace(/&[a-z]+;|&#\d+;/gi, ' ')
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '');

async function* htmlFiles(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* htmlFiles(path);
    else if (entry.name.endsWith('.html')) yield path;
  }
}

let files = 0;
let added = 0;
for await (const file of htmlFiles(DIST)) {
  const html = await readFile(file, 'utf8');
  const used = new Set(html.match(/id="([^"]+)"/g) || []);
  let touched = 0;

  const out = html.replace(HEADING, (whole, tag, attrs, inner) => {
    if (/\bid=/.test(attrs)) return whole; // never overwrite one that was written by hand
    const base = slugify(inner);
    if (!base) return whole;
    let id = base;
    for (let n = 2; used.has(`id="${id}"`); n++) id = `${base}-${n}`; // unique within the page
    used.add(`id="${id}"`);
    touched++;
    return `<${tag} id="${id}"${attrs}>${inner}</${tag}>`;
  });

  if (touched) {
    await writeFile(file, out);
    files++;
    added += touched;
  }
}

console.log(`[heading-ids] added ${added} heading ids across ${files} files`);
