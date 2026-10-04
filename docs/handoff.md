# Scraper Handoff

**Repo:** https://github.com/Badsati/-sativa  
**Purpose:** Scrape ExamTopics exam questions and store as JSON. Runs via GitHub Actions (rotating Azure IPs — server IP never exposed to ExamTopics).

---

## Repo Structure

```
scrape.py                        Non-interactive CLI scraper (used by CI)
main.py                          Interactive scraper (for local use)
providers.txt                    Ordered list of 95 providers to scrape (low priority excluded)
requirements.txt                 Python dependencies
examtopics/                      Scraper module
  fast_scanner.py                Primary: parallel HTTP scanner (64 workers)
  browser_scraper.py             Fallback: Camoufox stealth browser (Playwright)
  question_parser.py             Extracts question, options, votes, discussion
  http_client.py                 HTTP with retry, 429 backoff, connection pooling
  cache.py                       HTML cache (6h TTL)
  parsers.py                     HTML parsing, block detection
  matching.py                    Provider/slug/question number matching
  output.py                      Writes .json and .txt output files
  cleaner.py                     Strips popups and noise from HTML
  settings.py                    All config in one place
.github/workflows/scrape.yml     GitHub Actions workflow (cron every 6h + manual)
docs/handoff.md                  This file
data/                            Scraped output committed here, pulled by server
  {provider}/
    {exam}.json
```

---

## How It Works

1. **GitHub Actions** runs `scrape.py` on Ubuntu runners (Microsoft Azure IPs — not the server IP)
2. Workflow reads `providers.txt` top-to-bottom and picks the first provider with no `data/{provider}/` folder yet
3. Scraper auto-discovers all exams for the provider from `examtopics.com/exams/{provider}/`
4. For each exam: scans all discussion listing pages, finds question links, fetches each page
5. **Each exam commits and pushes immediately** after saving (incremental — no data lost on timeout)
6. Server cron pulls this repo every 6h and seeds new JSON into PostgreSQL

### Two scraping modes
- **Fast HTTP scanner** — plain HTTP, 64 parallel workers. Primary mode.
- **Camoufox fallback** — headless Firefox with humanized behavior. Kicks in automatically if HTTP is blocked by Cloudflare.

### Rate limiting protection
- 1.5–3s random delay between each question page fetch
- 429 responses trigger a 30s/60s/90s backoff retry (3 attempts) before giving up
- If a question still fails after retries, it's skipped and logged as `[WARN]`

---

## Workflow Behaviour

**Scheduled (every 6 hours):** Auto-picks next unscraped provider from `providers.txt`. Runs until that provider is fully done, then stops. Next cron tick picks the next provider.

**Manual trigger:** Go to **Actions → scrape → Run workflow**

| Input | Description |
|-------|-------------|
| `provider` | Force a specific provider (e.g. `sap`, `nutanix`). Leave blank to auto-pick. |
| `exam` | Single exam slug (e.g. `C1000-162`). Leave blank to scrape all exams. |

**Provider order in `providers.txt`:** Phase 2 high-priority first (IBM, SAP, CheckPoint...), then medium, then Phase 1 providers last (already in DB).

---

## Output Format

Each exam: `data/{provider}/{exam}.json` — array of question objects:

```json
[
  {
    "url": "https://www.examtopics.com/discussions/...",
    "question_no": "Topic 1 / Question 1",
    "question": "Which of the following...",
    "options": { "A": "...", "B": "...", "C": "...", "D": "..." },
    "most_voted": "B",
    "vote_counts": { "A": 3, "B": 12, "C": 1 },
    "community_answer": "B. Some answer text",
    "discussion": ["Selected Answer: B - reason...", "..."]
  }
]
```

---

## Server Setup (already done)

| What | Where |
|------|-------|
| Repo clone | `/opt/examience-data/` |
| Pull + seed script | `/opt/pull-and-seed.sh` |
| Cron schedule | Every 6h (`0 */6 * * *`) |
| Seed log | `/var/log/examience-seed.log` |

**How the server seeds:**
1. `git fetch` — checks for new commits on `origin/main`
2. If new commits: `git pull`, then count `.json` files in `data/`
3. Runs `DATA_DIR=/opt/examience-data/data npx tsx scripts/seed.ts`
4. Seed script deduplicates: skips questions already in DB by `(examId, questionNumber)`

**Check seed log:**
```bash
tail -f /var/log/examience-seed.log
```

**Manual seed trigger:**
```bash
/opt/pull-and-seed.sh
```

---

## Dedup Protection

Nothing will overwrite or duplicate DB data:

- **Layer 1** — within file: duplicate `questionNumber`s dropped before insert
- **Layer 2** — against DB: fetches existing `questionNumber`s for each `examId`, only inserts new ones
- **Layer 3** — pull script: exits early if no new git commits, seed never runs

---

## Re-scraping a Failed Exam

If an exam has missing questions (429 failures), to re-scrape:
1. Delete `data/{provider}/{exam}.json` from the repo
2. Commit and push
3. Trigger the workflow for that provider — it will re-scrape the deleted exam

---

## Current Status (2026-10-04)

- **IBM** — first run in progress (old workflow, commits at end of full run)
- All subsequent runs use incremental per-exam commits
- `scripts/seed.ts` in examience repo updated to read both `.md` and `.json`
- Server cron and pull script are live and waiting for data
