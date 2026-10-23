import random
import time
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from .http_client import HttpFetcher
from .parser import _first_question_no, parse_exam_page, parse_total_pages, parse_total_questions
from .settings import BASE_URL, DEFAULT_DELAY_RANGE


class CertLibraryScraper:
    def __init__(
        self,
        fetcher: HttpFetcher,
        delay_range: Tuple[float, float] = DEFAULT_DELAY_RANGE,
    ):
        self.fetcher = fetcher
        self.delay_range = delay_range

    def exam_url(self, exam_code: str, page: int = 1) -> str:
        url = f"{BASE_URL}/exam/{exam_code}"
        if page > 1:
            url += f"?page={page}"
        return url

    def fetch_all(self, exam_code: str) -> List[Dict]:
        first_html = self.fetcher.fetch_html(self.exam_url(exam_code, 1))
        total_pages = parse_total_pages(first_html)
        total_questions = parse_total_questions(first_html)
        first_q_no = _first_question_no(first_html)

        if total_questions:
            print(f"  Exam contains {total_questions} questions across {total_pages} pages")
        else:
            print(f"  Total pages: {total_pages}")

        all_questions: List[Dict] = []
        failed = 0

        with tqdm(total=total_pages, desc="Fetching pages", unit="page") as pbar:
            for page in range(1, total_pages + 1):
                url = self.exam_url(exam_code, page)
                try:
                    html = self.fetcher.fetch_html(url) if page > 1 else first_html

                    # Detect redirect loop: server sent page 1 again instead of page N
                    if page > 1 and _first_question_no(html) == first_q_no:
                        tqdm.write(
                            f"  [STOP] Page {page} returned page 1 content — "
                            "session cookie required for further pages."
                        )
                        break

                    qs = parse_exam_page(html, page_url=url)
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
