from pathlib import Path

from certlibrary.cache import HtmlCache
from certlibrary.http_client import HttpFetcher
from certlibrary.output import write_questions_to_json, write_questions_to_txt
from certlibrary.scraper import CertLibraryScraper
from certlibrary.settings import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_DELAY_RANGE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
)


def main():
    print()
    print("=" * 60)
    print("  CertLibrary Scraper")
    print("=" * 60)
    print()

    exam_code = prompt_required("Exam code (e.g. AZ-900, 200-301): ").upper()

    print()
    print("Session cookie — required for pages beyond page 1 (~50% of the exam).")
    print("How to get it:")
    print("  1. Log in to certlibrary.com in Chrome")
    print("  2. Open DevTools (F12) → Application → Cookies → www.certlibrary.com")
    print("  3. Copy the value of the 'sessionid' cookie")
    print()
    raw = input("Paste sessionid value (or press Enter to scrape page 1 only): ").strip()

    session_cookie: str | None = None
    if raw:
        session_cookie = raw if "=" in raw else f"sessionid={raw}"

    cache = HtmlCache(Path(DEFAULT_CACHE_DIR), DEFAULT_CACHE_TTL_SECONDS)
    fetcher = HttpFetcher(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        retries=DEFAULT_RETRIES,
        cache=cache,
        session_cookie=session_cookie,
    )

    scraper = CertLibraryScraper(fetcher, delay_range=DEFAULT_DELAY_RANGE)

    print(f"\nScraping {exam_code} from CertLibrary...\n")
    questions = scraper.fetch_all(exam_code)

    if not questions:
        print("No questions found. Check the exam code and try again.")
        return

    txt_file = f"{exam_code} questions.txt"
    json_file = f"{exam_code} questions.json"

    print()
    write_questions_to_txt(txt_file, questions)
    write_questions_to_json(json_file, questions)

    print(f"\nDone! {len(questions)} questions saved.")
    print(f"  → {txt_file}")
    print(f"  → {json_file}")


def prompt_required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("[ERROR] Value is required.\n")


if __name__ == "__main__":
    main()
