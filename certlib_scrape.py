import argparse
import subprocess
import sys
import time
from pathlib import Path

from certlibrary.browser_scraper import CertLibraryBrowserScraper
from certlibrary.cache import HtmlCache
from certlibrary.http_client import HttpFetcher
from certlibrary.output import write_questions_to_json
from certlibrary.scraper import CertLibraryScraper
from certlibrary.settings import (
    BASE_URL,
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_DELAY_RANGE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_MS,
    DEFAULT_TIMEOUT_SECONDS,
)

SEVEN_DAYS = 7 * 24 * 60 * 60


def _file_age_seconds(path: Path) -> float:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", str(path)],
        capture_output=True, text=True,
    )
    ts = result.stdout.strip()
    if ts:
        return time.time() - int(ts)
    return time.time() - path.stat().st_mtime


def discover_exams(vendor: str, fetcher: HttpFetcher) -> list[str]:
    """Fetch /vendor/VendorName/ and return all exam codes linked as /info/CODE."""
    import re
    url = f"{BASE_URL}/vendor/{vendor}/"
    try:
        html = fetcher.fetch_html(url)
        codes = re.findall(r'href="/info/([^"]+)"', html)
        return list(dict.fromkeys(codes))  # dedupe, preserve order
    except Exception as e:
        print(f"  [WARN] Could not discover exams for {vendor}: {e}")
        return []


def git_commit(vendor: str, exam: str, output_dir: Path) -> None:
    try:
        subprocess.run(["git", "add", str(output_dir)], check=True)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"certlib: {vendor}/{exam}"],
                check=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push"], check=True)
            print(f"  Committed and pushed {vendor}/{exam}")
    except Exception as e:
        print(f"  [WARN] Git commit failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", required=True, help="Vendor name as it appears on certlibrary.com")
    parser.add_argument("--exam", default=None, help="Single exam code (optional)")
    parser.add_argument("--output", default="certlib-data", help="Output root directory")
    parser.add_argument("--username", default=None, help="CertLibrary account email")
    parser.add_argument("--password", default=None, help="CertLibrary account password")
    parser.add_argument("--commit", action="store_true", help="Commit and push after each exam")
    args = parser.parse_args()

    vendor = args.vendor
    output_dir = Path(args.output) / vendor
    output_dir.mkdir(parents=True, exist_ok=True)

    use_browser = bool(args.username and args.password)

    if not use_browser:
        cache = HtmlCache(Path(DEFAULT_CACHE_DIR), DEFAULT_CACHE_TTL_SECONDS)
        fetcher = HttpFetcher(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            retries=DEFAULT_RETRIES,
            cache=cache,
        )
        scraper = CertLibraryScraper(fetcher, delay_range=DEFAULT_DELAY_RANGE)

    # Discover exam list using HTTP (no auth needed for vendor page)
    if args.exam:
        exams = [args.exam]
    else:
        print(f"Discovering exams for vendor: {vendor}")
        if use_browser:
            # Use a lightweight HTTP fetch just for discovery
            _cache = HtmlCache(Path(DEFAULT_CACHE_DIR), DEFAULT_CACHE_TTL_SECONDS)
            _fetcher = HttpFetcher(timeout=DEFAULT_TIMEOUT_SECONDS, retries=DEFAULT_RETRIES, cache=_cache)
            exams = discover_exams(vendor, _fetcher)
        else:
            exams = discover_exams(vendor, fetcher)
        if not exams:
            print(f"  No exams found for {vendor}. Skipping.")
            sys.exit(2)
        print(f"  Found {len(exams)} exam(s): {', '.join(exams[:10])}{'...' if len(exams) > 10 else ''}\n")

    did_work = False

    def _scrape_exams(get_questions):
        nonlocal did_work
        for exam in exams:
            out_file = output_dir / f"{exam}.json"

            if out_file.exists():
                age = _file_age_seconds(out_file)
                if age < SEVEN_DAYS:
                    print(f"  Skipping {exam} (scraped {age / 86400:.0f}d ago, still fresh)")
                    continue
                print(f"\n[{vendor}/{exam}] Re-scraping (data is {age / 86400:.0f}d old)...")
            else:
                print(f"\n[{vendor}/{exam}] Scraping...")

            try:
                questions = get_questions(exam)
            except Exception as e:
                print(f"  [WARN] Failed to scrape {exam}: {e}")
                continue

            if not questions:
                print(f"  No questions parsed for {exam}")
                continue

            write_questions_to_json(str(out_file), questions)
            if args.commit:
                git_commit(vendor, exam, output_dir)
            did_work = True
            time.sleep(5)

    if use_browser:
        with CertLibraryBrowserScraper(
            args.username,
            args.password,
            headless=True,
            timeout_ms=DEFAULT_TIMEOUT_MS,
            retries=DEFAULT_RETRIES,
            delay_range=DEFAULT_DELAY_RANGE,
        ) as browser:
            _scrape_exams(browser.fetch_all)
    else:
        _scrape_exams(scraper.fetch_all)

    if not did_work:
        sys.exit(2)


if __name__ == "__main__":
    main()
