"""Playwright JS-rendering extraction API (fallback for pages Trafilatura can't handle).

POST /extract  {url, wait_selector, wait_timeout, include_html}
  -> {url, title, content, html, error, extracted_at}
GET  /health   -> {status, service}
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright

app = FastAPI(title="Playwright Extract", version="1.0.0")

STRIP_SELECTORS = ["script", "style", "nav", "footer", "header", "iframe", "noscript"]
ASSET_GLOB = "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,otf,eot,ico}"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class ExtractRequest(BaseModel):
    url: str
    wait_selector: str = "body"
    wait_timeout: int = 10000
    include_html: bool = False


class ExtractResponse(BaseModel):
    url: str
    title: Optional[str] = None
    content: Optional[str] = None
    html: Optional[str] = None
    error: Optional[str] = None
    extracted_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return {"status": "ok", "service": "playwright"}


@app.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest):
    now = _now()
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = await browser.new_context(user_agent=UA)
            if not req.include_html:
                await ctx.route(ASSET_GLOB, lambda route: route.abort())
            page = await ctx.new_page()
            await page.goto(req.url, wait_until="domcontentloaded", timeout=req.wait_timeout)
            if req.wait_selector:
                try:
                    await page.wait_for_selector(req.wait_selector, timeout=req.wait_timeout)
                except Exception:  # noqa: BLE001
                    pass
            for sel in STRIP_SELECTORS:
                try:
                    await page.eval_on_selector_all(sel, "els => els.forEach(e => e.remove())")
                except Exception:  # noqa: BLE001
                    pass
            title = await page.title()
            content = await page.evaluate("() => document.body ? document.body.innerText : ''")
            html = await page.content() if req.include_html else None
            return ExtractResponse(url=req.url, title=title, content=content, html=html, extracted_at=now)
    except Exception as e:  # noqa: BLE001
        return ExtractResponse(url=req.url, error=f"render failed: {e}", extracted_at=now)
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
