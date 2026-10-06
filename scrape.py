import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

SEVEN_DAYS = 7 * 24 * 60 * 60

from examtopics.browser_scraper import CamoufoxScraper
from examtopics.cache import HtmlCache
from examtopics.fast_scanner import FastDiscussionScanner
from examtopics.http_client import HttpFetcher
from examtopics.matching import build_page_numbers, extract_topic_question, normalize_provider
from examtopics.output import write_questions_to_json
from examtopics.question_parser import parse_question_page
from examtopics.settings import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_DELAY_RANGE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_MS,
)

BASE_URL = "https://www.examtopics.com"


def get_exam_slugs(provider: str, fetcher: HttpFetcher) -> list:
    url = f"{BASE_URL}/exams/{provider}/"
    try:
        html = fetcher.fetch_html(url)
        slugs = re.findall(rf'/exams/{re.escape(provider)}/([^/"]+)/', html)
        return list(dict.fromkeys(slugs))
    except Exception as e:
        print(f"[WARN] Could not auto-discover exams for {provider}: {e}")
        return []


def _sample_exam_codes(provider: str, fetcher: HttpFetcher, pages: int = 3) -> set:
    """
    Return exam codes seen across the first `pages` of the discussions listing.
    Extracts just the exam slug from hrefs like:
      /discussions/redhat/view/12345-exam-ex200-topic-1-question-1-discussion/
    """
    from examtopics.matching import provider_discussion_url
    from examtopics.parsers import extract_discussion_entries
    codes = set()
    for page in range(1, pages + 1):
        try:
            html = fetcher.fetch_html(provider_discussion_url(provider, page))
            for _, href in extract_discussion_entries(html):
                # extract the exam code between "exam-" and "-topic"
                m = re.search(r"/exam-([^/]+?)-topic-", href, re.I)
                if m:
                    codes.add(m.group(1).lower())
        except Exception:
            break
    return codes


def _sanity_check_slug(provider: str, exam: str, fetcher: HttpFetcher, total_pages: int) -> bool:
    """
    Warn and return False only for large providers (>20 pages) when the exam slug
    isn't found in a proportional sample. Small providers always proceed to full scan.
    """
    if total_pages <= 20:
        return True  # cheap enough to scan fully — skip the check

    from examtopics.matching import normalize_slug
    sample_pages = min(5, total_pages // 10)
    needle = normalize_slug(exam)
    codes = _sample_exam_codes(provider, fetcher, pages=sample_pages)
    if not codes:
        return True  # couldn't sample — let the scan proceed
    if any(needle in normalize_slug(c) for c in codes):
        return True
    print(f"  [WARN] Slug '{exam}' not found in first {sample_pages} of {total_pages} discussion pages.")
    print(f"         Exam codes seen on ExamTopics: {', '.join(sorted(codes))}")
    print(f"         The exam may be named differently — skipping full scan.")
    return False


def _fetch_questions(links: list, fetcher: HttpFetcher) -> list:
    from tqdm import tqdm
    questions = []
    for url in tqdm(links, desc="Fetching Questions", unit="q"):
        try:
            html = fetcher.fetch_html(url)
            q = parse_question_page(html, url=url)
            questions.append(q)
        except Exception as e:
            tqdm.write(f"  [WARN] Failed {url}: {e}")
        time.sleep(random.uniform(1.5, 3.0))
    return questions


def _scrape_provider_single_pass(
    provider: str, fetcher: HttpFetcher, output_dir: Path, commit: bool
) -> bool:
    scanner = FastDiscussionScanner(provider, fetcher, delay_range=DEFAULT_DELAY_RANGE)
    try:
        total_pages = scanner.get_num_pages()
    except Exception as e:
        print(f"  [WARN] Could not get page count: {e}")
        return False

    page_numbers = build_page_numbers(total_pages, 1, None, None)
    print(f"  Single-pass: scanning {len(page_numbers)} discussion pages...")
    exam_links = scanner.scan_all_exams(page_numbers, workers=2)

    if not exam_links:
        print(f"  HTTP scan returned nothing — falling back to Camoufox browser...")
        try:
            with CamoufoxScraper(
                provider,
                headless=True,
                timeout_ms=DEFAULT_TIMEOUT_MS,
                retries=DEFAULT_RETRIES,
                delay_range=DEFAULT_DELAY_RANGE,
            ) as browser:
                exam_links = browser.scan_all_exams(page_numbers)
        except Exception as e:
            print(f"  [WARN] Camoufox fallback failed: {e}")

    if not exam_links:
        print(f"  No exam discussions found for {provider}")
        return False

    print(f"  Found {len(exam_links)} exams with discussions")
    did_work = False

    for exam, links in sorted(exam_links.items()):
        out_file = output_dir / f"{exam}.json"

        if out_file.exists():
            age = time.time() - out_file.stat().st_mtime
            if age < SEVEN_DAYS:
                print(f"  Skipping {exam} (data is {age / 86400:.0f}d old, fresh)")
                continue
            print(f"\n[{provider}/{exam}] Re-scraping ({len(links)} questions in discussions)...")
            questions = _fetch_questions(links, fetcher)
            if questions:
                slim = [
                    {"question": q["question"], "options": q["options"], "most_voted": q.get("most_voted", "")}
                    for q in questions
                ]
                existing = json.loads(out_file.read_text())
                existing_texts = {q["question"] for q in existing}
                new_qs = [q for q in slim if q["question"] not in existing_texts]
                if new_qs:
                    print(f"  {len(new_qs)} new questions found, merging")
                    write_questions_to_json(str(out_file), existing + new_qs)
                    if commit:
                        git_commit(provider, exam)
                    did_work = True
                else:
                    print(f"  No new questions for {exam}")
            continue

        print(f"\n[{provider}/{exam}] {len(links)} questions found, fetching...")
        questions = _fetch_questions(links, fetcher)
        if questions:
            slim = [
                {"question": q["question"], "options": q["options"], "most_voted": q.get("most_voted", "")}
                for q in questions
            ]
            write_questions_to_json(str(out_file), slim)
            if commit:
                git_commit(provider, exam)
            did_work = True
        else:
            print(f"  No questions parsed for {exam}")

    return did_work


def scrape_exam(provider: str, exam: str, fetcher: HttpFetcher, cache: HtmlCache) -> list:
    scanner = FastDiscussionScanner(provider, fetcher, delay_range=DEFAULT_DELAY_RANGE)
    try:
        total_pages = scanner.get_num_pages()
    except Exception as e:
        print(f"  [WARN] Could not get page count: {e}")
        return []

    if not _sanity_check_slug(provider, exam, fetcher, total_pages):
        return []

    page_numbers = build_page_numbers(total_pages, 1, None, None)
    print(f"  Scanning {len(page_numbers)} pages for '{exam}'...")
    links = scanner.scan(page_numbers, exam, workers=4)

    if not links:
        print(f"  No discussion links found for {exam}")
        return []

    links = sorted(links, key=extract_topic_question)
    print(f"  Found {len(links)} questions, fetching...")

    from tqdm import tqdm
    questions = []
    for url in tqdm(links, desc="Fetching Questions", unit="q"):
        try:
            html = fetcher.fetch_html(url)
            q = parse_question_page(html, url=url)
            questions.append(q)
        except Exception as e:
            tqdm.write(f"  [WARN] Failed {url}: {e}")
        time.sleep(random.uniform(1.5, 3.0))

    return questions


def git_commit(provider: str, exam: str) -> None:
    import subprocess
    try:
        subprocess.run(["git", "add", "data/"], check=True)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"data: {provider}/{exam}"],
                check=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push"], check=True)
            print(f"  Committed and pushed {provider}/{exam}")
    except Exception as e:
        print(f"  [WARN] Git commit failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--exam", default=None)
    parser.add_argument("--output", default="data")
    parser.add_argument("--commit", action="store_true", help="Commit and push after each exam")
    parser.add_argument("--legacy", action="store_true", help="Use old per-exam scan (fallback)")
    args = parser.parse_args()

    provider = normalize_provider(args.provider)
    output_dir = Path(args.output) / provider
    output_dir.mkdir(parents=True, exist_ok=True)

    cache = HtmlCache(Path(DEFAULT_CACHE_DIR), DEFAULT_CACHE_TTL_SECONDS)
    fetcher = HttpFetcher(
        timeout=DEFAULT_TIMEOUT_MS // 1000,
        retries=DEFAULT_RETRIES,
        cache=cache,
        refresh_cache=False,
    )

    did_work = False

    if not args.exam and not args.legacy:
        # New single-pass: scan all discussion pages once, collect by exam slug
        did_work = _scrape_provider_single_pass(provider, fetcher, output_dir, args.commit)
        if not did_work:
            sys.exit(2)
        return

    # Legacy path: per-exam scan (used when --exam or --legacy is passed)
    if args.exam:
        exams = [args.exam]
    else:
        print(f"Auto-discovering exams for '{provider}'...")
        exams = get_exam_slugs(provider, fetcher)
        if not exams:
            print("No exams found. Skipping.")
            sys.exit(2)
        print(f"Found {len(exams)} exams: {exams}\n")

    for exam in exams:
        out_file = output_dir / f"{exam}.json"
        if out_file.exists():
            age = time.time() - out_file.stat().st_mtime
            if age < SEVEN_DAYS:
                print(f"Skipping {exam} (data is {age / 86400:.0f}d old, fresh)")
                continue
            print(f"\n[{provider}/{exam}] Re-scraping (data is {age / 86400:.0f}d old)")
            questions = scrape_exam(provider, exam, fetcher, cache)
            if questions:
                slim = [
                    {"question": q["question"], "options": q["options"], "most_voted": q.get("most_voted", "")}
                    for q in questions
                ]
                existing = json.loads(out_file.read_text())
                existing_texts = {q["question"] for q in existing}
                new_qs = [q for q in slim if q["question"] not in existing_texts]
                if new_qs:
                    print(f"  {len(new_qs)} new questions found, merging")
                    write_questions_to_json(str(out_file), existing + new_qs)
                    if args.commit:
                        git_commit(provider, exam)
                    did_work = True
                else:
                    print(f"  No new questions")
            continue

        print(f"\n[{provider}/{exam}]")
        questions = scrape_exam(provider, exam, fetcher, cache)
        if questions:
            slim = [
                {"question": q["question"], "options": q["options"], "most_voted": q.get("most_voted", "")}
                for q in questions
            ]
            write_questions_to_json(str(out_file), slim)
            if args.commit:
                git_commit(provider, exam)
            did_work = True
        else:
            print(f"  No data for {exam}")

    if not did_work:
        sys.exit(2)  # signal to caller: nothing new scraped


if __name__ == "__main__":
    main()
