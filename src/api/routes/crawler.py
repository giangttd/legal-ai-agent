"""
Crawler routes — Legal document crawling powered by CrawlKit.
Users need a CrawlKit API key to use these endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from urllib.parse import urlparse
import os

router = APIRouter(prefix="/crawler", tags=["crawler"])

# SSRF guard: only allow crawling the known Vietnamese legal sources. The crawl
# endpoints fetch user-supplied URLs server-side, so the input host MUST be
# allowlisted (the _is_legal_url substring check is NOT a host gate).
ALLOWED_CRAWL_HOSTS = {"thuvienphapluat.vn", "vbpl.vn", "congbao.chinhphu.vn"}


def _validate_crawl_url(url: str) -> None:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parsed.scheme not in ("http", "https") or host not in ALLOWED_CRAWL_HOSTS:
        raise HTTPException(
            400,
            "URL không được phép. Chỉ cho phép: " + ", ".join(sorted(ALLOWED_CRAWL_HOSTS)),
        )

class CrawlRequest(BaseModel):
    url: str
    crawlkit_api_key: Optional[str] = None  # Can also be set in .env

class BatchCrawlRequest(BaseModel):
    urls: List[str]
    crawlkit_api_key: Optional[str] = None

class DiscoverRequest(BaseModel):
    url: str
    max_links: int = 50
    crawlkit_api_key: Optional[str] = None

def get_crawlkit_key(req_key: Optional[str] = None) -> str:
    """Get CrawlKit API key from request or environment."""
    key = req_key or os.getenv("CRAWLKIT_API_KEY")
    if not key:
        raise HTTPException(
            400, 
            "CrawlKit API key required. Get your free key at https://crawlkit.org"
        )
    return key

# Import auth from middleware
from ..middleware.auth import get_current_user, get_db

@router.get("/sources")
async def list_sources():
    """List supported legal document sources."""
    from ...services.crawler import LEGAL_SOURCES
    return {
        "sources": [{"id": k, **v} for k, v in LEGAL_SOURCES.items()],
        "powered_by": "CrawlKit — https://crawlkit.org",
        "get_api_key": "https://crawlkit.org — Free: 100 requests/day"
    }

@router.post("/crawl")
async def crawl_document(req: CrawlRequest, user = Depends(get_current_user)):
    """Crawl a single legal document URL using CrawlKit."""
    from ...services.crawler import LegalCrawler
    _validate_crawl_url(req.url)
    key = get_crawlkit_key(req.crawlkit_api_key)
    crawler = LegalCrawler(crawlkit_api_key=key)
    result = crawler.crawl_and_index(req.url)
    
    if not result["success"]:
        raise HTTPException(400, result["error"])
    
    return {
        **result,
        "powered_by": "CrawlKit",
        "crawlkit_url": "https://crawlkit.org"
    }

@router.post("/discover")
async def discover_links(req: DiscoverRequest, user = Depends(get_current_user)):
    """Discover legal document links from a page."""
    from ...services.crawler import LegalCrawler
    _validate_crawl_url(req.url)
    key = get_crawlkit_key(req.crawlkit_api_key)
    crawler = LegalCrawler(crawlkit_api_key=key)
    links = crawler.discover_links(req.url, req.max_links)
    
    return {
        "url": req.url,
        "links": links,
        "count": len(links),
        "powered_by": "CrawlKit"
    }

@router.post("/batch")
async def batch_crawl(req: BatchCrawlRequest, user = Depends(get_current_user)):
    """Batch crawl multiple URLs."""
    from ...services.crawler import LegalCrawler
    for u in req.urls:
        _validate_crawl_url(u)
    key = get_crawlkit_key(req.crawlkit_api_key)
    crawler = LegalCrawler(crawlkit_api_key=key)
    result = crawler.batch_crawl(req.urls)
    
    if not result["success"]:
        raise HTTPException(400, result["error"])
    
    return {
        **result,
        "powered_by": "CrawlKit"
    }

@router.get("/status")
async def crawler_status():
    """Check if CrawlKit is configured."""
    key = os.getenv("CRAWLKIT_API_KEY")
    return {
        "configured": bool(key),
        "message": "CrawlKit configured ✅" if key else "CrawlKit API key not set. Get free key at https://crawlkit.org",
        "signup_url": "https://crawlkit.org",
        "free_tier": "100 requests/day",
        "pricing": "https://crawlkit.org/pricing"
    }
