BASE_URL = "https://www.certlibrary.com"

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_TIMEOUT_MS = DEFAULT_TIMEOUT_SECONDS * 1000
DEFAULT_RETRIES = 3
DEFAULT_DELAY_RANGE = (3.0, 7.0)
MAX_HTTP_POOL_SIZE = 4
DEFAULT_CACHE_DIR = ".certlibrary_cache"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}
