"""Trafilatura content-extraction API.

POST /extract  {url, include_links, include_images, include_tables}
  -> {url, title, author, date, content, error, extracted_at}
GET  /health   -> {status, service}
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
import trafilatura
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Trafilatura Extract", version="1.0.0")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class ExtractRequest(BaseModel):
    url: str
    include_links: bool = False
    include_images: bool = False
    include_tables: bool = True


class ExtractResponse(BaseModel):
    url: str
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    content: Optional[str] = None
    error: Optional[str] = None
    extracted_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return {"status": "ok", "service": "trafilatura"}


@app.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest):
    now = _now()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(req.url, headers={"User-Agent": UA})
            resp.raise_for_status()
            html = resp.text
    except Exception as e:  # noqa: BLE001
        return ExtractResponse(url=req.url, error=f"fetch failed: {e}", extracted_at=now)

    try:
        content = trafilatura.extract(
            html,
            url=req.url,
            include_links=req.include_links,
            include_images=req.include_images,
            include_tables=req.include_tables,
            favor_precision=True,
        )
        meta = trafilatura.extract_metadata(html)
        return ExtractResponse(
            url=req.url,
            title=getattr(meta, "title", None) if meta else None,
            author=getattr(meta, "author", None) if meta else None,
            date=getattr(meta, "date", None) if meta else None,
            content=content,
            error=None if content else "no content extracted (page may be JS-rendered; try Playwright)",
            extracted_at=now,
        )
    except Exception as e:  # noqa: BLE001
        return ExtractResponse(url=req.url, error=f"extract failed: {e}", extracted_at=now)
