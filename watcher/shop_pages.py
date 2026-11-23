"""Read shop search result pages with plain HTTP and regex: no browser, no AI, no API key.

Each parser takes the result page HTML and returns in-stock listings in page order. Page layouts change;
when a parser returns nothing for a query that should match, compare the page with the test snippets."""
import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html import unescape
from typing import Literal
from urllib.parse import quote, urljoin

import httpx
from pydantic import BaseModel

from watcher import cache
from watcher.models import Listing

@dataclass(frozen=True)
class Chain:
    name: str
    url: str         # search URL with {q}
    site: str        # browser allowlist pattern
    mark: str        # start of one result block on the search page
    no_results: str  # text the shop shows when nothing matches

    def search_url(self, keywords: str) -> str:
        return self.url.format(q=quote(keywords))


CHAINS = {
    "hardoff": Chain("Hard Off", "https://netmall.hardoff.co.jp/search/?q={q}&exso=1", "*.hardoff.co.jp",
                     '<div class="itemcolmn_item', "該当する商品が見つかりませんでした"),
    "bookoff": Chain("BOOKOFF", "https://shopping.bookoff.co.jp/search/keyword/{q}", "*.bookoff.co.jp",
                     'class="productItem__inner"', "該当する商品が見つかりませんでした"),
    "trefac": Chain("Treasure Factory", "https://ec.treasure-f.com/search?word={q}", "*.treasure-f.com",
                    'class="pj-search_item"', "検索結果（0件）"),
    "surugaya": Chain("Surugaya", "https://www.suruga-ya.jp/search?category=&search_word={q}", "*.suruga-ya.jp",
                      '<div class="item">', "検索結果は０件です"),
}


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _text(s: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _find(pattern: str, block: str) -> str | None:
    m = re.search(pattern, block, re.S)
    return m.group(1) if m else None


def _yen(s: str | None) -> int | None:
    return int(s.replace(",", "")) if s else None


def _item(base: str, url, title, price, image=None, grade=None, branch=None, branch_url=None) -> Listing | None:
    if not url or not title:
        return None
    return Listing(url=urljoin(base, unescape(url)), title=_text(title), price=_yen(price), why_matches="",
                   matches=True, verified=False, image_url=urljoin(base, image) if image else None,
                   condition_grade=grade.upper() if grade else None, branch=_text(branch) if branch else None,
                   branch_url=branch_url)


def _blocks(key: str, html: str) -> list[str]:
    return html.split(CHAINS[key].mark)[1:]


def _hardoff_title(b: str) -> str:
    return " ".join(_text(t) for t in re.findall(r'class="item-(?:brand-name|name|code)">(.*?)</div>', b, re.S))


def parse_hardoff(html: str) -> list[Listing]:
    return [i for b in _blocks("hardoff", html) if (i := _item(
        "https://netmall.hardoff.co.jp/", _find(r'<a href="([^"]+)"', b), _hardoff_title(b),
        _find(r'item-price-en">\s*([\d,]+)', b), _find(r'<img[^>]*?\ssrc="([^"]+)"', b),
        _find(r'icon-rank-[^"]*"\s+alt="([^"]+)"', b)))]


def parse_bookoff(html: str) -> list[Listing]:
    return [i for b in _blocks("bookoff", html) if "在庫あり" in b and (i := _item(
        "https://shopping.bookoff.co.jp/", _find(r'href="(/used/[^"]+)"', b),
        _find(r'productItem__title">(.*?)</p>', b), _find(r'&yen;\s*([\d,]+)', b),
        _find(r'<img[^>]*?\ssrc="([^"]+)"', b)))]


def parse_trefac(html: str) -> list[Listing]:
    return [i for b in _blocks("trefac", html) if "cm-itemlist_soldout" not in b and (i := _item(
        "https://ec.treasure-f.com/", _find(r'href="(/item/\d+)"', b), _find(r'alt="([^"]+)"', b),
        _find(r'cm-itemlist_price[^>]*>\s*￥([\d,]+)', b), _find(r'<img[^>]*?\ssrc="([^"]+)"', b),
        branch=_find(r'cm-itemlist_shop_link.*?<span>(.*?)</span>', b),
        branch_url=f"https://www.treasure-f.com/shop/{sid}/" if (sid := _find(r'cm-itemlist_shop_link.*?shop=(\d+)', b))
        else None))]


def parse_surugaya(html: str) -> list[Listing]:
    """Used price only (lowest when several sellers); sold-out (品切れ) and new-only items are skipped."""
    return [i for b in _blocks("surugaya", html) if "中古：" in b and "品切れ" not in b and (i := _item(
        "https://www.suruga-ya.jp/", _find(r'href="(https://www\.suruga-ya\.jp/product/detail/[^"]+)"', b),
        _find(r'class="product-name">(.*?)</h3>', b), _find(r'中古：.*?￥([\d,]+)', b),
        _find(r'<img[^>]*?\ssrc="([^"]+)"', b)))]


PARSERS = {"hardoff": parse_hardoff, "bookoff": parse_bookoff, "trefac": parse_trefac, "surugaya": parse_surugaya}


class LayoutChanged(Exception):
    """The page loaded but neither listings nor the shop's 'no results' text were found: the parser needs updating."""


async def fetch_shop(key: str, keywords: str, max_price: int | None = None,
                     transport: httpx.AsyncBaseTransport | None = None) -> list[Listing]:
    """One chain's in-stock results within budget. Raises on HTTP errors and LayoutChanged (caller reports the shop
    as failed). An honest 'no results' page (Hard Off sends it with status 404) returns []."""
    chain = CHAINS[key]
    async with httpx.AsyncClient(timeout=20, transport=transport, headers={"User-Agent": UA}) as client:
        resp = await client.get(chain.search_url(keywords))
        if "Redirecting to" in resp.text[:3000]:  # first visit only sets a cookie; the same client sends it back
            resp = await client.get(chain.search_url(keywords))
    if chain.no_results in resp.text and resp.status_code in (200, 404):
        return []
    resp.raise_for_status()
    items = [i.model_copy(update={"shop": chain.name}) for i in PARSERS[key](resp.text)]
    if not items and chain.mark not in resp.text:  # blocks present but none kept (e.g. all sold out) is fine
        raise LayoutChanged(chain.name)
    return [i for i in items if max_price is None or (i.price is not None and i.price <= max_price)]


# --- pick-up: which branch holds an item, and its street address (the train-time site accepts addresses) ---
def _address(raw: str | None) -> str | None:
    """'〒980-0871宮城県仙台市青葉区八幡3-1-50 レキシントンプラザ八幡2F' -> '宮城県仙台市青葉区八幡3-1-50' (building dropped)."""
    if not raw:
        return None
    return _text(re.sub(r"〒?\d{3}-\d{4}", " ", _text(raw))).split(" ")[0] or None


def parse_hardoff_item(html: str) -> tuple[str | None, str | None]:
    """Hard Off item page -> (branch name, address). Search results don't say which branch has the item."""
    name = _find(r'"seller":\{[^}]*"name":"([^"]+)"', html)
    return (unescape(name) if name else None,
            _address(_find(r'product-detail-store__address">(.*?)</p>', html)))


def parse_trefac_shop(html: str) -> str | None:
    """Treasure Factory branch page -> address."""
    return _address(_find(r'class="add_area">(.*?)</p>', html))


async def add_branches(items: list[Listing], limit: int = 10,
                       transport: httpx.AsyncBaseTransport | None = None) -> list[Listing]:
    """Pick-up mode: fill branch and branch_station (its address) for each shop's `limit` cheapest items,
    from Hard Off's item page or Treasure Factory's branch page. Pages that fail are skipped; order is kept."""
    def page(i: Listing) -> str | None:
        return {"Hard Off": i.url, "Treasure Factory": i.branch_url}.get(i.shop or "")

    todo: list[str] = []
    for shop in ("Hard Off", "Treasure Factory"):
        cheapest = sorted((i for i in items if i.shop == shop and not i.branch_station and page(i)),
                          key=lambda i: (i.price is None, i.price or 0))
        todo += list(dict.fromkeys(page(i) for i in cheapest))[:limit]
    limit_host = asyncio.Semaphore(3)

    places: dict[str, tuple[str | None, str | None]] = {}  # page url -> (branch, address); on disk for 30 days

    async def fetch(client: httpx.AsyncClient, url: str):
        if found := cache.get("place", url):
            places[url] = tuple(found)
            return
        async with limit_host:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                return  # not cached: try again next search
        places[url] = parse_hardoff_item(resp.text) if "hardoff" in url else (None, parse_trefac_shop(resp.text))
        cache.put("place", url, places[url])

    async with httpx.AsyncClient(timeout=20, transport=transport, headers={"User-Agent": UA}) as client:
        await asyncio.gather(*(fetch(client, u) for u in todo))

    def fill(i: Listing) -> Listing:
        name, address = places.get(page(i) or "", (None, None))
        if not address:
            return i
        return i.model_copy(update={"branch": name or i.branch, "branch_station": address})

    return [fill(i) for i in items]


# --- plan and ranking (shared by the plugin tools and the multi-agent graph) ---
class Plan(BaseModel):
    target: Literal["shops", "marketplaces"] = "shops"  # marketplaces (Mercari…) stay with the single agent
    keywords: str
    max_price: int | None = None
    mode: Literal["ship", "pickup"] = "ship"
    home_station: str | None = None
    chains: list[str] = list(CHAINS)


CommuteFn = Callable[[str, str], Awaitable[str]]

_CHAIN_PREFIX = re.compile(
    r"^(ハードオフ|オフハウス|ホビーオフ|ガレージオフ|モードオフ|ブックオフ|BOOKOFF|トレジャーファクトリー|トレファク)"
    r"(工具館|スーパーバザー)?\s*"
)


def branch_place(branch: str | None) -> str | None:
    """'ハードオフ白岡店' -> '白岡': branch names usually carry the place, which is usually a station name."""
    if not branch:
        return None
    place = _CHAIN_PREFIX.sub("", branch.strip()).removesuffix("店").strip()
    return place or None


_GRADES = {g: i for i, g in enumerate(["N", "S", "A", "B", "C", "D", "J"])}
_MIN = re.compile(r"(\d+) min")


async def rank(plan: Plan, items: list[Listing], commute: CommuteFn | None) -> list[Listing]:
    """ship: cheapest first, better grade breaks ties. pickup: shortest train trip first, then price."""
    by_price = sorted(items, key=lambda i: (i.price is None, i.price or 0, _GRADES.get((i.condition_grade or "?")[0], 9)))
    if plan.mode != "pickup" or not plan.home_station or commute is None:
        return by_price
    where = lambda i: i.branch_station or branch_place(i.branch)  # note: name-based guess when no station given
    stations = sorted({where(i) for i in items if where(i)})
    found = await asyncio.gather(*(commute(plan.home_station, s) for s in stations))
    minutes = {s: int(m[1]) for s, t in zip(stations, found) if (m := _MIN.search(t))}
    ranked = sorted(by_price, key=lambda i: minutes.get(where(i), 10**6))  # stable: keeps price order
    note = lambda i: " · ".join(filter(None, [i.why_matches, f"{minutes[where(i)]} min from {plan.home_station}"]))
    return [i.model_copy(update={"why_matches": note(i)}) if where(i) in minutes else i for i in ranked]
