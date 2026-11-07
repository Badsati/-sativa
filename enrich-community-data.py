import argparse
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

THIRTY_DAYS = 30 * 24 * 60 * 60

from examtopics.cache import HtmlCache
from examtopics.fast_scanner import FastDiscussionScanner
from examtopics.http_client import HttpFetcher
from examtopics.matching import build_page_numbers, extract_topic_question, normalize_provider
from examtopics.question_parser import parse_question_page
from examtopics.settings import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_DELAY_RANGE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_MS,
)


def _needs_enrichment(q: dict) -> bool:
    return not q.get("vote_counts") and not q.get("discussion")


def _stem(text: str) -> str:
    """Normalize question text to a comparable stem for matching."""
    m = re.search(r"\bA[.)]\s+", text)
    core = text[: m.start()] if m else text
    return re.sub(r"\s+", " ", core).strip().lower()[:100]


def git_commit(provider: str, exam: str) -> None:
    try:
        subprocess.run(["git", "add", "data/"], check=True)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"community-data: {provider}/{exam}"],
                check=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push"], check=True)
            print(f"  Committed and pushed {provider}/{exam}")
    except Exception as e:
        print(f"  [WARN] Git commit failed: {e}")


def enrich_exam(
    provider: str,
    exam: str,
    out_file: Path,
    fetcher: HttpFetcher,
    cache: HtmlCache,
    commit: bool,
) -> bool:
    existing: list[dict] = json.loads(out_file.read_text())

    to_enrich = [q for q in existing if _needs_enrichment(q)]
    if not to_enrich:
        print(f"  [{provider}/{exam}] all questions already enriched — skipping")
        return False

    print(f"  [{provider}/{exam}] {len(to_enrich)}/{len(existing)} questions need enrichment")

    stem_index: dict[str, int] = {}
    for idx, q in enumerate(existing):
        s = _stem(q.get("question", ""))
        if s:
            stem_index[s] = idx

    scanner = FastDiscussionScanner(provider, fetcher, delay_range=DEFAULT_DELAY_RANGE)
    try:
        total_pages = scanner.get_num_pages()
    except Exception as e:
        print(f"  [WARN] Could not get page count for {provider}: {e}")
        return False

    page_numbers = build_page_numbers(total_pages, 1, None, None)
    print(f"  Scanning {len(page_numbers)} discussion pages for '{exam}'...")
    links = scanner.scan(page_numbers, exam, workers=4)

    if not links:
        print(f"  No discussion links found for {exam}")
        return False

    links = sorted(links, key=extract_topic_question)
    print(f"  Found {len(links)} question links — fetching to extract community data...")

    updated = 0
    for url in links:
        try:
            html = fetcher.fetch_html(url)
            parsed = parse_question_page(html, url=url)
        except Exception as e:
            print(f"  [WARN] Failed {url}: {e}")
            time.sleep(random.uniform(1.0, 2.0))
            continue

        votes = parsed.get("vote_counts") or {}
        discussion = parsed.get("discussion") or []

        if not votes and not discussion:
            time.sleep(random.uniform(1.0, 2.0))
            continue

        s = _stem(parsed.get("question", ""))
        idx = stem_index.get(s)
        if idx is None:
            time.sleep(random.uniform(1.0, 2.0))
            continue

        entry = existing[idx]
        if _needs_enrichment(entry):
            if votes:
                entry["vote_counts"] = votes
            if discussion:
                entry["discussion"] = discussion
            updated += 1

        time.sleep(random.uniform(1.0, 2.0))

    if updated == 0:
        print(f"  No matches enriched for {exam}")
        return False

    out_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    print(f"  Updated {updated} questions in {exam}")

    if commit:
        git_commit(provider, exam)

    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--exam", default=None)
    parser.add_argument("--output", default="data")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    provider = normalize_provider(args.provider)
    output_dir = Path(args.output) / provider

    if not output_dir.exists():
        print(f"No data directory for provider '{provider}' — skipping")
        sys.exit(2)

    # Sentinel: skip providers enriched within the last 30 days
    sentinel = output_dir / ".community-data-enriched"
    if not args.exam and sentinel.exists():
        age = time.time() - float(sentinel.read_text().strip())
        if age < THIRTY_DAYS:
            remaining = (THIRTY_DAYS - age) / 86400
            print(f"Skipping '{provider}': enriched {age / 86400:.1f}d ago. Next in {remaining:.1f}d.")
            sys.exit(2)

    cache = HtmlCache(Path(DEFAULT_CACHE_DIR), DEFAULT_CACHE_TTL_SECONDS)
    fetcher = HttpFetcher(
        timeout=DEFAULT_TIMEOUT_MS // 1000,
        retries=DEFAULT_RETRIES,
        cache=cache,
        refresh_cache=False,
    )

    if args.exam:
        exams = [args.exam]
    else:
        exams = [
            f.stem
            for f in output_dir.glob("*.json")
            if not f.name.startswith(".")
        ]
        if not exams:
            print(f"No exam JSON files found for '{provider}'")
            sys.exit(2)

    did_work = False

    for exam in sorted(exams):
        out_file = output_dir / f"{exam}.json"
        if not out_file.exists():
            print(f"  [{provider}/{exam}] JSON file not found — skipping")
            continue

        try:
            enriched = enrich_exam(provider, exam, out_file, fetcher, cache, args.commit)
            if enriched:
                did_work = True
        except Exception as e:
            print(f"  [WARN] Failed enriching {exam}: {e}")

    if not args.exam:
        sentinel.write_text(str(int(time.time())))
        if args.commit:
            try:
                subprocess.run(["git", "add", str(sentinel)], check=True)
                r = subprocess.run(["git", "diff", "--staged", "--quiet"])
                if r.returncode != 0:
                    subprocess.run(
                        ["git", "commit", "-m", f"chore: mark {provider} community-data enriched"],
                        check=True,
                    )
                    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
                    subprocess.run(["git", "push"], check=True)
            except Exception as e:
                print(f"  [WARN] Could not commit sentinel: {e}")

    if not did_work:
        sys.exit(2)


if __name__ == "__main__":
    main()
