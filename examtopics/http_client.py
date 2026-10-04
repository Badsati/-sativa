import threading
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache import HtmlCache
from .parsers import raise_if_blocked
from .settings import MAX_HTTP_POOL_SIZE, REQUEST_HEADERS


class SessionFactory:
    def __init__(self, pool_size: int = MAX_HTTP_POOL_SIZE):
        self.pool_size = pool_size
        self._thread_local = threading.local()

    def get(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            return session

        retry = Retry(
            total=0,
            connect=0,
            read=0,
            redirect=2,
            status=0,
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(
            pool_connections=self.pool_size,
            pool_maxsize=self.pool_size,
            max_retries=retry,
        )
        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        self._thread_local.session = session
        return session


class HttpFetcher:
    def __init__(
        self,
        *,
        timeout: int,
        retries: int,
        cache: Optional[HtmlCache],
        refresh_cache: bool = False,
        session_factory: Optional[SessionFactory] = None,
    ):
        self.timeout = timeout
        self.retries = retries
        self.cache = cache
        self.refresh_cache = refresh_cache
        self.session_factory = session_factory or SessionFactory()

    def fetch_html(self, url: str) -> str:
        if self.cache is not None and not self.refresh_cache:
            cached_html = self.cache.read(url)
            if cached_html is not None:
                return cached_html

        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session_factory.get().get(url, timeout=self.timeout)
                response.raise_for_status()
                raise_if_blocked(response.text)
                if self.cache is not None:
                    self.cache.write(url, response.text)
                return response.text
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(1.0 * attempt)

        raise RuntimeError(f"Failed to load {url}: {last_error}")

