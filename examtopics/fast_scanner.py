import random
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Sequence, Tuple

from tqdm import tqdm

from .http_client import HttpFetcher
from .matching import (
    dedupe,
    discussion_entry_url,
    extract_topic_question,
    matches_discussion_entry,
    normalize_provider,
    provider_discussion_url,
)
from .parsers import extract_discussion_entries, parse_discussion_page_count
from .settings import DEFAULT_DELAY_RANGE


class FastDiscussionScanner:
    def __init__(
        self,
        provider: str,
        fetcher: HttpFetcher,
        *,
        delay_range: Tuple[float, float] = DEFAULT_DELAY_RANGE,
    ):
        self.provider = normalize_provider(provider)
        self.fetcher = fetcher
        self.delay_range = delay_range

    def get_num_pages(self) -> int:
        html = self.fetcher.fetch_html(provider_discussion_url(self.provider))
        return parse_discussion_page_count(html)

    def fetch_page_links(self, page_number: int, search_string: str) -> List[str]:
        html = self.fetcher.fetch_html(provider_discussion_url(self.provider, page_number))
        links = []

        for text, href in extract_discussion_entries(html):
            if matches_discussion_entry(text, href, search_string):
                links.append(discussion_entry_url(text, href))

        low, high = self.delay_range
        if high > 0:
            time.sleep(random.uniform(max(low, 0), max(high, low)))
        return dedupe(links)

    def _fetch_all_entries(self, page_number: int) -> List[Tuple[str, str]]:
        """Return (exam_slug, question_url) pairs from a single discussion listing page."""
        html = self.fetcher.fetch_html(provider_discussion_url(self.provider, page_number))
        results = []
        for text, href in extract_discussion_entries(html):
            m = re.search(r"-exam-([^/]+?)-topic-", href, re.I)
            if m:
                slug = m.group(1).lower()
                url = discussion_entry_url(text, href)
                results.append((slug, url))
        low, high = self.delay_range
        if high > 0:
            time.sleep(random.uniform(max(low, 0), max(high, low)))
        return results

    def scan_all_exams(self, page_numbers: Sequence[int], workers: int = 4) -> Dict[str, List[str]]:
        """
        Single-pass scan across all discussion pages.
        Returns {exam_slug: [sorted question_urls]} for every exam found.
        """
        all_links: Dict[str, List[str]] = defaultdict(list)
        pages = list(page_numbers)

        errors = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(self._fetch_all_entries, page): page
                for page in pages
            }
            with tqdm(total=len(pages), desc="Scanning discussions", unit="page") as pbar:
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        for slug, url in future.result():
                            all_links[slug].append(url)
                    except Exception as exc:
                        errors += 1
                        tqdm.write(f"  [WARN] Page {page} failed: {exc}")
                    pbar.update(1)

        if errors:
            pct = errors * 100 // len(pages)
            print(f"  [WARN] {errors}/{len(pages)} pages failed ({pct}%) — results may be incomplete")

        return {
            slug: dedupe(sorted(links, key=extract_topic_question))
            for slug, links in all_links.items()
        }

    def scan(self, page_numbers: Sequence[int], search_string: str, workers: int) -> List[str]:
        links = []
        pages = list(page_numbers)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(self.fetch_page_links, page_number, search_string): page_number
                for page_number in pages
            }
            with tqdm(total=len(pages), desc="Fetching Links", unit="page") as pbar:
                for future in as_completed(futures):
                    page_number = futures[future]
                    try:
                        links.extend(future.result())
                    except Exception as exc:
                        print(f"\nError on page {page_number}: {exc}")
                    pbar.update(1)

        return dedupe(links)

