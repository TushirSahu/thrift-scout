"""Real shop pages: catches layout changes the snippet tests can't. Skipped by default; run with `pytest -m live`."""
import pytest

from watcher.shop_pages import PARSERS, fetch_shop
from watcher.shops import CHAINS
from watcher.tools import lookup_commute

pytestmark = pytest.mark.live
QUERY = {"hardoff": "WH-1000XM5", "bookoff": "ワンピース", "trefac": "WH-1000XM5", "surugaya": "ニンテンドースイッチ"}  # things these shops always stock


@pytest.mark.parametrize("key", sorted(PARSERS))
async def test_shop_page_still_parses(key):
    items = await fetch_shop(key, QUERY[key])
    host = CHAINS[key].site.removeprefix("*")
    assert items, f"{key}: no listings"
    assert all(i.url.startswith("https://") and host in i.url for i in items)
    assert sum(1 for i in items if i.price and i.price > 0) >= len(items) * 0.9


@pytest.mark.parametrize("key", sorted(PARSERS))
async def test_shop_no_results_page_is_empty(key):
    assert await fetch_shop(key, "zqxjvwk9183") == []


async def test_train_times_still_parse():
    assert "min" in await lookup_commute("吹田", "江坂")


async def test_market_price_still_parses():
    from watcher import market
    m = await market.market_price("WH-1000XM5")
    assert m and m["count"] >= 3 and 5000 < m["median"] < 60000
