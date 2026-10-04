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
  fast_scanner.py                Primary: parallel HTTP scanner (1 worker — rate-limit safe)
  browser_scraper.py             Fallback: Camoufox stealth browser (Playwright)
  question_parser.py             Extracts question, options, votes, discussion
  http_client.py                 HTTP with retry, 429 backoff (30/60/90s), connection pooling
  cache.py                       HTML cache (6h TTL)
  parsers.py                     HTML parsing, block detection
  matching.py                    Provider/slug/question number matching
  output.py                      Writes .json and .txt output files
  cleaner.py                     Strips popups and noise from HTML
  settings.py                    All config in one place
.github/workflows/scrape.yml     GitHub Actions workflow (cron every 6h + manual trigger)
docs/handoff.md                  This file
data/                            Scraped output committed here, pulled by server
  {provider}/
    {exam}.json
```

---

## How It Works

1. **GitHub Actions** runs `scrape.py` on Ubuntu runners (Microsoft Azure IPs — not the server IP)
2. Workflow reads `providers.txt` top-to-bottom, picks first provider with no `data/{provider}/` folder
3. Scraper auto-discovers all exams from `examtopics.com/exams/{provider}/`
4. For each exam: scans all discussion pages, finds question links, fetches each question page
5. **Each exam commits and pushes immediately** after saving (incremental — no data lost on timeout)
6. Server cron polls every 5 minutes, pulls new commits, seeds into PostgreSQL automatically

### Two scraping modes
- **Fast HTTP scanner** — plain HTTP, sequential (1 worker). Primary mode.
- **Camoufox fallback** — headless Firefox, humanized. Kicks in if HTTP is blocked by Cloudflare.

### Rate limiting protection
- 1 worker (sequential page scanning) — avoids bursting
- 1.5–3s random delay between each question page fetch
- 429 responses trigger 30s/60s/90s backoff retry (3 attempts) before skipping

---

## JSON Output Format

Each exam: `data/{provider}/{exam}.json` — array of slim question objects:

```json
[
  {
    "question": "Which of the following...",
    "options": { "A": "...", "B": "...", "C": "...", "D": "..." },
    "most_voted": "B"
  }
]
```

Only these 3 fields are saved. Everything else (vote counts, discussion, URL, community answer) is stripped before writing.

---

## Workflow Behaviour

**Scheduled (every 6h):** Auto-picks next unscraped provider from `providers.txt`. Commits after each exam.

**Manual trigger:** Actions → scrape → Run workflow

| Input | Description |
|-------|-------------|
| `provider` | Force a specific provider (e.g. `sap`, `nutanix`). Leave blank to auto-pick. |
| `exam` | Single exam slug. Leave blank to scrape all exams for the provider. |

**Important:** Always trigger a **new run** (not rerun) to pick up latest code changes.

---

## Server Setup

| What | Where |
|------|-------|
| Repo clone | `/opt/examience-data/` |
| Pull + seed script | `/opt/pull-and-seed.sh` |
| Cron schedule | Every 5 min (`*/5 * * * *`) |
| Seed log | `/var/log/examience-seed.log` |

**How seeding works:**
1. `git fetch` — checks for new commits on `origin/main`
2. If new commits: `git pull`, count `.json` files in `data/`
3. Runs `DATA_DIR=/opt/examience-data/data npx tsx scripts/seed.ts`
4. Lock file (`/tmp/seed.lock`) prevents overlapping runs
5. Seed deduplicates: skips questions already in DB by `(examId, questionNumber)`

**Check seed log:**
```bash
tail -f /var/log/examience-seed.log
```

**Manual seed trigger:**
```bash
/opt/pull-and-seed.sh
```

---

## Seed Script (examience repo)

`scripts/seed.ts` handles both `.md` (Phase 1) and `.json` (Phase 2) files:

- `.json` parser: reads `question`, `options` (converts to `{id, text}[]`), `most_voted` → `correctIds`
- Skips questions with empty options (paywall-locked on ExamTopics)
- Skips questions already in DB (dedup by `examId + questionNumber`)
- Batch inserts in groups of 50

---

## Dedup Protection — Nothing Overwrites the DB

- **Layer 1** — within file: duplicate `questionNumber`s dropped before insert
- **Layer 2** — against DB: only inserts questions not already present
- **Layer 3** — pull script exits early if no new git commits, seed never runs
- **Layer 4** — lock file prevents overlapping seed runs

---

## Provider Order (`providers.txt`)

Phase 2 high-priority first (IBM → SAP → CheckPoint...), then medium priority, then Phase 1 providers last (already in DB, scraped for freshness).

95 providers total. Low priority (💤) excluded.

---

## Re-scraping a Failed Exam

If an exam has missing questions (429 failures mid-run):
1. Delete `data/{provider}/{exam}.json` from the repo and push
2. Trigger the workflow for that provider — it re-scrapes the deleted exam

---

## GitHub Setup

- **PAT_TOKEN** secret required in repo settings (Settings → Secrets → Actions)
- PAT must have `repo` scope and be authorized for the `Badsati` org
- Without it, commits will fail with 403

---

## Current Status (2026-10-04)

- IBM scraping in progress — committing per exam, data appearing live in `data/ibm/`
- SAP is next after IBM completes
- Server cron live, seeding automatically within 5 min of each commit
- `scripts/seed.ts` in examience updated to read `.json` alongside `.md`
- Questions with no options (paywall) are filtered at seed time
- Slim JSON format (question/options/most_voted only) applied from next provider onwards
