---
name: web-research
description: "Web search + content extraction via the in-pod web-tools stack: SearXNG (search), Trafilatura (fast extraction), Playwright (JS-rendered fallback)."
version: 1.0.0
author: corv
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Web, Search, Scraping, Research, SearXNG, Trafilatura, Playwright]
    related_skills: [searxng-search, container-web-infra]
---

# Web Research (search + extract)

Research the web using the in-pod web-tools stack. All three services run in the
same pod and are reachable on localhost. No external API keys are required.

| Service      | URL (env)                 | Purpose                                       |
|--------------|---------------------------|-----------------------------------------------|
| SearXNG      | `$SEARXNG_URL` (:8080)    | Meta search (Google/DDG/Brave/Bing/Wikipedia) |
| Trafilatura  | `$TRAFILATURA_URL` (:8000)| Fast static content extraction                |
| Playwright   | `$PLAYWRIGHT_URL` (:8001) | JS-rendered extraction (fallback)             |

## When to Use

- The user asks to look something up, research a topic, or fetch current info.
- You need the content of a specific URL (article, docs page, etc.).
- A page is JS-heavy and a plain fetch returns nothing useful.

## Use this, not the built-in web tools

Hermes ships built-in `web_search` / `web_extract` / `browser_*` tools, but in
this deployment extraction is not configured — `web_extract`/`browser` expect
external Firecrawl/Browserbase services we don't run, so they fail or return
nothing. Ignore any generic coaching to "use web_extract, not curl." For search
and extraction here, always use the in-pod services below (`web_search` is the
one exception — it's already wired to SearXNG, so built-in search and this skill
agree). Reaching Trafilatura/Playwright by `curl` to the pod endpoints is the
correct path, not a workaround.

## Flow

1. **Search (find URLs)** — SearXNG. Hermes' native web-search provider already
   uses `$SEARXNG_URL` automatically. You can also call it directly:
   ```
   curl -s "${SEARXNG_URL}/search?q=<urlencoded query>&format=json&pageno=1"
   ```
   Parse `.results[]` -> `{title, url, content}`. Pick the top relevant URLs.

2. **Extract a URL (fast path)** — Trafilatura:
   ```
   curl -s -X POST "${TRAFILATURA_URL}/extract" \
     -H 'content-type: application/json' \
     -d '{"url":"https://example.com/article"}'
   ```
   Returns `{url, title, author, date, content, error, extracted_at}`.

3. **Fallback (if Trafilatura `content` is empty or `error` says JS-rendered)** —
   Playwright renders the page in a real browser:
   ```
   curl -s -X POST "${PLAYWRIGHT_URL}/extract" \
     -H 'content-type: application/json' \
     -d '{"url":"https://example.com/spa","wait_selector":"main","wait_timeout":10000}'
   ```
   Returns `{url, title, content, html, error, extracted_at}`.

4. **Synthesize** the extracted content into your answer and cite the source URLs.

## Tips & Constraints

- Trafilatura is fast and cheap — always try it first. Use Playwright only when it
  returns empty/no content (SPAs, JS-rendered pages); Playwright is much slower.
- For a specific known URL, skip search and go straight to extract.
- The endpoints are pod-internal (localhost). Never expose them publicly.
- SearXNG aggregates public engines; be reasonable with request volume.
