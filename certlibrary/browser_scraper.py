import random
import time
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from .parser import _first_question_no, parse_exam_page, parse_total_pages, parse_total_questions
from .settings import BASE_URL, DEFAULT_DELAY_RANGE, DEFAULT_RETRIES, DEFAULT_TIMEOUT_MS

try:
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ImportError:
    Camoufox = None
    PlaywrightError = Exception
    PlaywrightTimeoutError = TimeoutError


class CertLibraryBrowserScraper:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        headless: bool = True,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        retries: int = DEFAULT_RETRIES,
        delay_range: Tuple[float, float] = DEFAULT_DELAY_RANGE,
    ):
        self.username = username
        self.password = password
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.delay_range = delay_range
        self._manager = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        if Camoufox is None:
            raise RuntimeError("Camoufox missing. Run: pip install camoufox")

        self._manager = Camoufox(
            headless=self.headless,
            humanize=True,
            locale="en-US",
            enable_cache=True,
            window=(1366, 900),
        )
        self.browser = self._manager.__enter__()
        self.context = self.browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self._login()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.context:
            self.context.close()
        if self._manager:
            self._manager.__exit__(exc_type, exc, tb)

    def _login(self):
        print("  Logging in to CertLibrary...")
        self.page.goto(f"{BASE_URL}/accounts/login/", wait_until="domcontentloaded")

        self.page.fill("input[name='username'], input[name='email'], input[type='email']", self.username)
        self.page.fill("input[name='password'], input[type='password']", self.password)
        self.page.click("button[type='submit'], input[type='submit']")

        try:
            self.page.wait_for_url(lambda url: "/login" not in url, timeout=15_000)
        except PlaywrightTimeoutError:
            pass

        if "/login" in self.page.url:
            raise RuntimeError("Login failed — check CERTLIB_USER and CERTLIB_PASS credentials")

        print("  Logged in successfully")

    def _fetch_page_html(self, url: str) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:
                    self.page.wait_for_selector("div.card.question_card", timeout=10_000)
                except PlaywrightTimeoutError:
                    pass
                try:
                    self.page.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightTimeoutError:
                    pass
                return self.page.content()
            except (PlaywrightError, Exception) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(2.0 * attempt)

        raise RuntimeError(f"Failed to load {url}: {last_error}")

    def fetch_all(self, exam_code: str) -> List[Dict]:
        base_url = f"{BASE_URL}/exam/{exam_code}"

        first_html = self._fetch_page_html(base_url)
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
                try:
                    html = first_html if page == 1 else self._fetch_page_html(f"{base_url}?page={page}")

                    if page > 1 and _first_question_no(html) == first_q_no:
                        tqdm.write(
                            f"  [STOP] Page {page} still shows page 1 — "
                            "account may not have access beyond page 1."
                        )
                        break

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
