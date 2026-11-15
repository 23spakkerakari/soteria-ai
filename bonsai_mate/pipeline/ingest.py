"""Step 1: fetch one public page or PDF and save its plain text under data/raw/.

data/raw/ is gitignored. Publicly readable is not the same as reusable:
check the source's license before turning its text into benchmark scenarios.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from backend import config

USER_AGENT = "bonsai-mate-ingest/0.1"


def _allowed_by_robots(url: str) -> bool:
    parts = urlparse(url)
    robots = RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        robots.read()
    except OSError:
        return True  # no readable robots.txt
    return robots.can_fetch(USER_AGENT, url)


def _pdf_text(data: bytes) -> str:
    import fitz  # PyMuPDF

    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _html_text(data: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    node = soup.find("main") or soup.find("article") or soup.body or soup
    return node.get_text("\n", strip=True)


def ingest(target: str, organization: str, source_type: str) -> Path:
    """target is a URL or a local file path. Returns the saved .txt path."""
    local = Path(target)
    if local.exists():
        data, url, is_pdf = local.read_bytes(), None, local.suffix.lower() == ".pdf"
        name = local.stem
    else:
        if not _allowed_by_robots(target):
            raise PermissionError(f"robots.txt disallows fetching {target}")
        res = httpx.get(target, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)
        res.raise_for_status()
        data, url = res.content, target
        is_pdf = "pdf" in res.headers.get("content-type", "") or target.lower().endswith(".pdf")
        parts = urlparse(target)
        name = f"{parts.netloc}{parts.path}"

    text = _pdf_text(data) if is_pdf else _html_text(data)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = config.RAW_DIR / f"{slug}.txt"
    out.write_text(text, encoding="utf-8")
    out.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "organization": organization,
                "source_type": source_type,
                "source_url": url,
                "license_verified": False,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out
