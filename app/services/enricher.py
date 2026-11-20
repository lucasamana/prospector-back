import asyncio
import re
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

WA_URL_RE = re.compile(
    r"https?://(?:wa\.me|api\.whatsapp\.com/send|web\.whatsapp\.com/send)[^\s\"'<>]+",
    re.IGNORECASE,
)
IG_EXCLUDED_PATHS = {
    "p", "reel", "reels", "stories", "explore", "accounts", "tv", "share", "sharer", "direct"
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return u


def _extract_whatsapp_clean(href: str) -> tuple[str | None, str | None]:
    """Devuelve (whatsapp_url_limpia, digitos_telefono) si detecta un enlace de WhatsApp."""
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()

    if "wa.me" in host:
        digits = re.sub(r"\D", "", parsed.path)
        if len(digits) >= 10:
            return f"https://wa.me/{digits}", digits
        return href.split("?")[0], None

    if "whatsapp.com" in host:
        qs = parse_qs(parsed.query)
        phone_vals = qs.get("phone") or []
        if phone_vals:
            digits = re.sub(r"\D", "", phone_vals[0])
            if len(digits) >= 10:
                return f"https://wa.me/{digits}", digits
        return href, None

    return None, None


def _extract_instagram_clean(href: str) -> str | None:
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if "instagram.com" not in host:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    handle = parts[0].strip().lstrip("@")
    if not handle or handle.lower() in IG_EXCLUDED_PATHS:
        return None
    return f"https://instagram.com/{handle}"


async def _enrich_single_lead(
    lead: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict:
    website = _normalize_url(lead.get("website"))
    if not website:
        return lead

    lead["website"] = website

    # Caso 1: el propio campo website de Google Maps ya es WhatsApp o Instagram
    wa_direct, wa_digits = _extract_whatsapp_clean(website)
    if wa_direct:
        lead["whatsapp_url"] = wa_direct
        lead["tipo_telefono"] = "whatsapp_verificado"
        if wa_digits and not lead.get("telefono_normalizado"):
            lead["telefono_normalizado"] = f"+{wa_digits}"
        return lead

    ig_direct = _extract_instagram_clean(website)
    if ig_direct:
        lead["instagram"] = ig_direct
        return lead

    # Caso 2: scrapear el HTML del sitio / Linktree
    async with semaphore:
        try:
            resp = await client.get(website)
            if resp.status_code >= 400 or "text/html" not in resp.headers.get("content-type", ""):
                return lead
            html = resp.text
        except Exception:
            return lead

    soup = BeautifulSoup(html, "html.parser")
    found_wa: str | None = None
    found_wa_digits: str | None = None
    found_ig: str | None = lead.get("instagram")

    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if not found_wa and ("wa.me" in href.lower() or "whatsapp.com" in href.lower()):
            wa_url, digits = _extract_whatsapp_clean(href)
            if wa_url:
                found_wa, found_wa_digits = wa_url, digits
        if not found_ig and "instagram.com" in href.lower():
            ig_url = _extract_instagram_clean(href)
            if ig_url:
                found_ig = ig_url
        if found_wa and found_ig:
            break

    # Fallback sobre HTML crudo (botones armados con JS o onclick)
    if not found_wa:
        match = WA_URL_RE.search(html)
        if match:
            found_wa, found_wa_digits = _extract_whatsapp_clean(match.group(0))

    if found_wa:
        lead["whatsapp_url"] = found_wa
        lead["tipo_telefono"] = "whatsapp_verificado"
        if found_wa_digits and (
            not lead.get("telefono_normalizado") or lead.get("tipo_telefono") == "fijo"
        ):
            lead["telefono_normalizado"] = f"+{found_wa_digits}"

    if found_ig:
        lead["instagram"] = found_ig

    return lead


async def enrich_leads_batch(
    leads: list[dict],
    concurrency: int = 10,
    timeout: float = 5.0,
) -> list[dict]:
    if not leads:
        return leads

    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers=HEADERS,
    ) as client:
        tasks = [_enrich_single_lead(lead, client, semaphore) for lead in leads]
        return list(await asyncio.gather(*tasks))
