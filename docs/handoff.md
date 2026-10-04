# Scraper Handoff

**Repo:** https://github.com/Badsati/-sativa  
**Purpose:** Scrape ExamTopics exam questions and store as JSON. Runs via GitHub Actions (rotating IPs — server IP never exposed to ExamTopics).

---

## Repo Structure

```
scrape.py                        Non-interactive CLI scraper (used by CI)
main.py                          Interactive scraper (for local use)
requirements.txt                 Python dependencies
examtopics/                      Scraper module
  fast_scanner.py                Primary: parallel HTTP scanner (64 workers)
  browser_scraper.py             Fallback: Camoufox stealth browser (Playwright)
  question_parser.py             Extracts question, options, votes, discussion
  http_client.py                 HTTP with retry and connection pooling
  cache.py                       HTML cache (6h TTL)
  parsers.py                     HTML parsing, block detection
  matching.py                    Provider/slug/question number matching
  output.py                      Writes .json and .txt output files
  cleaner.py                     Strips popups and noise from HTML
  settings.py                    All config in one place
.github/workflows/scrape.yml     GitHub Actions workflow
docs/handoff.md                  This file
data/                            Scraped output (committed here, pulled by server)
  {provider}/
    {exam}.json
```

---

## How It Works

1. **GitHub Actions** runs `scrape.py` on Ubuntu runners (Microsoft Azure IPs — not the server IP)
2. The scraper auto-discovers all exams for a provider from `examtopics.com/exams/{provider}/`
3. For each exam it scans all discussion listing pages, finds question links, fetches each question page
4. Questions saved to `data/{provider}/{exam}.json` and committed back to this repo
5. The server pulls this repo and seeds the JSON into the PostgreSQL DB

### Two scraping modes
- **Fast HTTP scanner** — plain HTTP requests with browser User-Agent, 64 parallel workers. Works unless IP is flagged.
- **Camoufox fallback** — headless Firefox with humanized behavior, bypasses JS popups and Cloudflare. Kicks in automatically if HTTP gets blocked.

---

## Triggering a Scrape

Go to **Actions → scrape → Run workflow**

| Input | Description |
|-------|-------------|
| `provider` | Provider slug as it appears on ExamTopics (e.g. `ibm`, `sap`, `nutanix`) |
| `exam` | *(optional)* Single exam slug (e.g. `C1000-162`). Leave blank to scrape all exams for the provider. |

Results are automatically committed to `data/{provider}/`.

---

## Output Format

Each exam produces a `{exam}.json` file — a JSON array of question objects:

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

## Seeding into the DB

The examience app's seed script currently reads `.md` files. To seed from these `.json` files, the seed script needs to be adapted to accept the JSON format above.

**Server pull location:** `/opt/examience-data/` (to be set up — server pulls this repo here)

**Seed command (once adapted):**
```bash
DATA_DIR=/opt/examience-data npx tsx scripts/seed.ts
```

---

## Providers to Scrape (Phase 2)

These are not in the existing GitHub cache and need live scraping:

| Provider | ExamTopics Slug | Exam Count | Priority |
|----------|----------------|------------|----------|
| IBM | ibm | 112 | 🔥 |
| SAP | sap | 62 | 🔥 |
| NetApp | netapp | 34 | ⭐ |
| CheckPoint | checkpoint | 22 | 🔥 |
| GIAC | giac | 20 | ⭐ |
| Nutanix | nutanix | 16 | ⭐ |
| CyberArk | cyberark | 11 | ⭐ |
| Workday | workday | 11 | ⭐ |
| NVIDIA | nvidia | 8 | ⭐ |
| F5 | f5 | 8 | ⭐ |
| IAPP | iapp | 7 | ⭐ |
| MuleSoft | mulesoft | 5 | ⭐ |
| Veeam | veeam | 4 | ⭐ |
| Atlassian | atlassian | 4 | ⭐ |
| RedHat | redhat | 3 | 🔥 |
| MongoDB | mongodb | 2 | ⭐ |
| Confluent | confluent | 2 | ⭐ |
| Zscaler | zscaler | 2 | ⭐ |
| Aruba | aruba | 2 | ⭐ |
| CNCF | cncf | 2 | ⭐ |
| ISTQB | istqb | 21 | ⭐ |
| SailPoint | sailpoint | 1 | 💤 |
| DataDog | datadog | 1 | ⭐ |

---

## Notes

- Only free public questions are scraped. Questions behind contributor access come back empty — that is ExamTopics paywall, not a bug.
- Cache is stored in `.examtopics_cache/` (gitignored) with a 6h TTL.
- Re-running a workflow skips already-scraped exams (file existence check).
- Phase 2 Docker container on the server (`exams-download-phase2-1`) is still running in parallel — whichever finishes first can be seeded.
