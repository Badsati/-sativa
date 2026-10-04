import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Sequence, Tuple

from tqdm import tqdm

from .http_client import HttpFetcher
from .matching import (
    dedupe,
    discussion_entry_url,
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

