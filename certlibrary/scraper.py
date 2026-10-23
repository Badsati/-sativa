import random
import time
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from .http_client import HttpFetcher
from .parser import _first_question_no, parse_csrf_token, parse_exam_page, parse_total_pages, parse_total_questions
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
        base_url = f"{BASE_URL}/exam/{exam_code}"
        first_html = self.fetcher.fetch_html(base_url)
        total_pages = parse_total_pages(first_html)
        total_questions = parse_total_questions(first_html)
        first_q_no = _first_question_no(first_html)
        csrf_token = parse_csrf_token(first_html)

        if total_questions:
            print(f"  Exam contains {total_questions} questions across {total_pages} pages")
        else:
            print(f"  Total pages: {total_pages}")

        if total_pages > 1 and not csrf_token:
            print("  [WARN] No CSRF token found — pagination may fail without a valid session cookie")

        all_questions: List[Dict] = []
        failed = 0

        with tqdm(total=total_pages, desc="Fetching pages", unit="page") as pbar:
            for page in range(1, total_pages + 1):
                try:
                    if page == 1:
                        html = first_html
                    else:
                        # CertLibrary uses POST form navigation, not GET ?page=N
                        html = self.fetcher.post_html(
                            base_url,
                            data={
                                "csrfmiddlewaretoken": csrf_token or "",
                                "page": str(page),
                                "qpp_arrow_buttons": "",
                                "recaptcha-result": "",
                            },
                            cache_key=f"{exam_code}:page:{page}",
                        )
                        # Detect redirect: server returned page 1 again (expired/missing cookie)
                        if _first_question_no(html) == first_q_no:
                            tqdm.write(
                                f"  [STOP] Page {page} returned page 1 content — "
                                "session cookie may be expired or missing."
                            )
                            break
                        # Refresh CSRF token from each response for the next POST
                        new_csrf = parse_csrf_token(html)
                        if new_csrf:
                            csrf_token = new_csrf

                    qs = parse_exam_page(html, page_url=f"{base_url}?page={page}")
                    first_q = qs[0]["question_no"] if qs else "(none)"
                    tqdm.write(f"  [DEBUG] Page {page}: first question = {first_q}")
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
