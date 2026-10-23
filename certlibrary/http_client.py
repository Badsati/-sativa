import hashlib
import time
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache import HtmlCache
from .settings import MAX_HTTP_POOL_SIZE, REQUEST_HEADERS


class HttpFetcher:
    def __init__(
        self,
        *,
        timeout: int,
        retries: int,
        cache: Optional[HtmlCache],
        session_cookie: Optional[str] = None,
        refresh_cache: bool = False,
    ):
        self.timeout = timeout
        self.retries = retries
        self.cache = cache
        self.refresh_cache = refresh_cache
        self._session = self._build_session(session_cookie)

    def _build_session(self, session_cookie: Optional[str]) -> requests.Session:
        retry = Retry(total=0, connect=0, read=0, redirect=2, status=0)
        adapter = HTTPAdapter(
            pool_connections=MAX_HTTP_POOL_SIZE,
            pool_maxsize=MAX_HTTP_POOL_SIZE,
            max_retries=retry,
        )
        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)
        # Use cookie jar instead of raw Cookie header so the server's
        # Set-Cookie responses (csrftoken, etc.) are accepted and forwarded
        if session_cookie:
            for pair in session_cookie.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    session.cookies.set(name.strip(), value.strip(), domain="www.certlibrary.com")
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def fetch_html(self, url: str) -> str:
        if self.cache is not None and not self.refresh_cache:
            cached = self.cache.read(url)
            if cached is not None:
                return cached

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = 30 * attempt
                    print(f"  [429] rate limited — waiting {wait}s before retry {attempt}/{self.retries}")
                    time.sleep(wait)
                    last_error = RuntimeError("429 Too Many Requests")
                    continue
                resp.raise_for_status()
                if self.cache is not None:
                    self.cache.write(url, resp.text)
                return resp.text
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(1.0 * attempt)

        raise RuntimeError(f"Failed to load {url}: {last_error}")

    def post_html(self, url: str, data: Dict, cache_key: Optional[str] = None) -> str:
        """POST form data and return response HTML. Optionally cache by cache_key."""
        if cache_key and self.cache is not None and not self.refresh_cache:
            digest = hashlib.sha1(cache_key.encode()).hexdigest()
            cached = self.cache.read(f"post:{digest}")
            if cached is not None:
                return cached

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self._session.post(url, data=data, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = 30 * attempt
                    print(f"  [429] rate limited — waiting {wait}s before retry {attempt}/{self.retries}")
                    time.sleep(wait)
                    last_error = RuntimeError("429 Too Many Requests")
                    continue
                resp.raise_for_status()
                if cache_key and self.cache is not None:
                    digest = hashlib.sha1(cache_key.encode()).hexdigest()
                    self.cache.write(f"post:{digest}", resp.text)
                return resp.text
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(1.0 * attempt)

        raise RuntimeError(f"Failed to POST {url}: {last_error}")
