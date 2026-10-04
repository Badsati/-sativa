from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Sequence

from tqdm import tqdm

from .cleaner import clean_examtopics_html
from .http_client import HttpFetcher
from .matching import slug_from_url


class CleanPageExporter:
    def __init__(self, fetcher: HttpFetcher):
        self.fetcher = fetcher

    def export_one(self, url: str, output_dir: Path) -> Path:
        html = self.fetcher.fetch_html(url)
        clean_html = clean_examtopics_html(html, url)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{slug_from_url(url)}.html"
        output_path.write_text(clean_html, encoding="utf-8")
        return output_path

    def export_many(self, links: Sequence[str], output_dir: Path, workers: int) -> List[Path]:
        output_paths = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(self.export_one, link, output_dir): link for link in links}
            with tqdm(total=len(futures), desc="Saving Clean Pages", unit="page") as pbar:
                for future in as_completed(futures):
                    link = futures[future]
                    try:
                        output_paths.append(future.result())
                    except Exception as exc:
                        print(f"\nClean page failed for {link}: {exc}")
                    pbar.update(1)
        return output_paths

