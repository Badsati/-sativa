import argparse
import random
import re
import sys
import time
from pathlib import Path

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


def _sample_discussion_slugs(provider: str, fetcher: HttpFetcher) -> list:
    """Return a sample of exam codes seen on page 1 of the discussions listing."""
    from examtopics.matching import provider_discussion_url
    from examtopics.parsers import extract_discussion_entries
    try:
        html = fetcher.fetch_html(provider_discussion_url(provider, 1))
        entries = extract_discussion_entries(html)
        codes = set()
        for text, href in entries:
            # pull out tokens that look like exam codes (letters/digits and hyphens)
            for token in re.findall(r"[A-Z0-9]+-[A-Z0-9][\w-]*", f"{text} {href}", re.I):
                codes.add(token.lower())
        return sorted(codes)
    except Exception:
        return []


def _sanity_check_slug(provider: str, exam: str, fetcher: HttpFetcher) -> bool:
    """
    Warn and return False when the exam slug doesn't appear in the first page of
    the provider's discussion listing — a strong signal the scan will find nothing.
    """
    from examtopics.matching import normalize_slug
    needle = normalize_slug(exam)
    samples = _sample_discussion_slugs(provider, fetcher)
    if not samples:
        return True  # couldn't sample — let the scan proceed
    if any(needle in normalize_slug(s) for s in samples):
        return True
    print(f"  [WARN] Slug '{exam}' (normalized: '{needle}') not found on discussions page 1.")
    print(f"         Sample codes seen: {', '.join(samples[:20])}")
    print(f"         The exam may be named differently on ExamTopics — skipping full scan.")
    return False


def scrape_exam(provider: str, exam: str, fetcher: HttpFetcher, cache: HtmlCache) -> list:
    scanner = FastDiscussionScanner(provider, fetcher, delay_range=DEFAULT_DELAY_RANGE)
    try:
        total_pages = scanner.get_num_pages()
    except Exception as e:
        print(f"  [WARN] Could not get page count: {e}")
        return []

    if not _sanity_check_slug(provider, exam, fetcher):
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

    if args.exam:
        exams = [args.exam]
    else:
        print(f"Auto-discovering exams for '{provider}'...")
        exams = get_exam_slugs(provider, fetcher)
        if not exams:
            print("No exams found. Exiting.")
            sys.exit(1)
        print(f"Found {len(exams)} exams: {exams}\n")

    for exam in exams:
        out_file = output_dir / f"{exam}.json"
        if out_file.exists():
            print(f"Skipping {exam} (already exists)")
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
        else:
            print(f"  No data for {exam}")


if __name__ == "__main__":
    main()
