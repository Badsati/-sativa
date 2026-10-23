import random
import time
from typing import Dict, List, Tuple

from tqdm import tqdm

from .parser import parse_exam_page, parse_total_pages, parse_total_questions
from .settings import BASE_URL, DEFAULT_DELAY_RANGE


class ItExamsScraper:
    def __init__(self, fetcher, delay_range: Tuple[float, float] = DEFAULT_DELAY_RANGE):
        self.fetcher = fetcher
        self.delay_range = delay_range

    def exam_url(self, slug: str, page: int = 1) -> str:
        if page == 1:
            return f"{BASE_URL}/exam/{slug}"
        return f"{BASE_URL}/exam/{slug}/{page}"

    def fetch_all(self, slug: str) -> List[Dict]:
        first_html = self.fetcher.fetch_html(self.exam_url(slug))
        total_pages = parse_total_pages(first_html)
        total_questions = parse_total_questions(first_html)

        if total_questions:
            print(f"  Exam contains {total_questions} questions across {total_pages} pages")
        else:
            print(f"  Total pages: {total_pages}")

        all_questions: List[Dict] = []
        failed = 0

        with tqdm(total=total_pages, desc="Fetching pages", unit="page") as pbar:
            for page in range(1, total_pages + 1):
                try:
                    html = first_html if page == 1 else self.fetcher.fetch_html(self.exam_url(slug, page))
                    qs = parse_exam_page(html, page_url=self.exam_url(slug, page))
                    all_questions.extend(qs)
                except Exception as exc:
                    failed += 1
                    tqdm.write(f"  [WARN] Page {page} failed: {exc}")

                pbar.update(1)

                if page < total_pages:
                    low, high = self.delay_range
                    time.sleep(random.uniform(low, high))

        if failed:
            print(f"  [WARN] {failed} page(s) failed — results may be incomplete")

        return all_questions
