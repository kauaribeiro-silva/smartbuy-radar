from __future__ import annotations

import asyncio
import logging
from typing import Optional
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}


async def fetch_html_httpx(url: str, timeout: float = 12.0) -> Optional[str]:
    """Fast HTTPX fetch for pages that don't require full JS execution."""
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=timeout,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
            logger.debug("HTTPX status %s for %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("HTTPX failed for %s: %s", url, exc)
    return None


async def fetch_html_playwright(url: str, wait_ms: int = 1800) -> Optional[str]:
    """Headless Chromium fetch with playwright-stealth for JS-heavy e-commerce pages."""
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        logger.warning("Playwright or playwright-stealth not available: %s", exc)
        return None

    timeout_ms = settings.scrape_timeout_seconds * 1000
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=settings.headless_browser,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1366, "height": 768},
                user_agent=DEFAULT_HEADERS["User-Agent"],
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await asyncio.sleep(wait_ms / 1000.0)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        logger.debug("Playwright failed for %s: %s", url, exc)
        return None


async def fetch_page_smart(url: str, force_browser: bool = False) -> Optional[str]:
    """Try fast HTTPX first, falling back to Playwright stealth if blocked or empty."""
    if not force_browser:
        html = await fetch_html_httpx(url)
        if html and not _looks_blocked_or_empty(html):
            return html
    return await fetch_html_playwright(url)


def _looks_blocked_or_empty(html: str) -> bool:
    lower = html.lower()
    if len(lower) < 1500:
        return True
    block_markers = [
        "enable javascript",
        "cf-browser-verification",
        "just a moment...",
        "robot or human",
        "captcha",
        "acesso negado",
    ]
    return any(marker in lower for marker in block_markers)
