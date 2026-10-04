import argparse
import re
import sys
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


def scrape_exam(provider: str, exam: str, fetcher: HttpFetcher, cache: HtmlCache) -> list:
    scanner = FastDiscussionScanner(provider, fetcher, delay_range=DEFAULT_DELAY_RANGE)
    try:
        total_pages = scanner.get_num_pages()
    except Exception as e:
        print(f"  [WARN] Could not get page count: {e}")
        return []

    page_numbers = build_page_numbers(total_pages, 1, None, None)
    print(f"  Scanning {len(page_numbers)} pages for '{exam}'...")
    links = scanner.scan(page_numbers, exam, workers=4)

    if not links:
        print(f"  No discussion links found for {exam}")
        return []

    links = sorted(links, key=extract_topic_question)
    print(f"  Found {len(links)} questions, fetching...")

    questions = []
    for url in links:
        try:
            html = fetcher.fetch_html(url)
            q = parse_question_page(html, url=url)
            questions.append(q)
        except Exception as e:
            print(f"  [WARN] Failed {url}: {e}")

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
            write_questions_to_json(str(out_file), questions)
            if args.commit:
                git_commit(provider, exam)
        else:
            print(f"  No data for {exam}")


if __name__ == "__main__":
    main()
