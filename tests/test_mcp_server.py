import httpx
import pytest

import watcher.mcp_server as srv
from watcher.models import Listing
from watcher.shop_pages import LayoutChanged



@pytest.fixture(autouse=True)
def no_market(monkeypatch):  # no real Yahoo calls in unit tests
    async def none(keywords):
        return None
    monkeypatch.setattr(srv.market, "market_price", none)

def L(url, price, branch=None):
    return Listing(url=url, title=f"item {price}", price=price, why_matches="", matches=True, verified=False,
                   branch=branch)


async def test_search_shops_merges_ranks_and_reports_failures(monkeypatch):
    async def fetch(key, keywords, max_price):
        if key == "trefac":
            raise httpx.HTTPStatusError("403", request=None, response=None)
        return {"hardoff": [L("https://h.jp/1", 9000), L("javascript:x", 1)], "bookoff": [L("https://b.jp/1", 7000)]}[key]

    monkeypatch.setattr(srv, "fetch_shop", fetch)
    out = await srv.search_shops("WH-1000XM5", chains=["hardoff", "bookoff", "trefac", "nope"])
    assert [r["price"] for r in out["results"]] == [7000, 9000]   # junk link cleaned, cheapest first
    assert out["failed"] == ["Treasure Factory (could not load)"] and "untrusted" in out["note"]


async def test_layout_change_is_reported(monkeypatch):
    async def fetch(key, keywords, max_price):
        raise LayoutChanged("BOOKOFF")

    monkeypatch.setattr(srv, "fetch_shop", fetch)
    out = await srv.search_shops("x", chains=["bookoff"])
    assert out["failed"] == ["BOOKOFF (page layout changed)"] and out["results"] == []


async def test_search_shops_pickup_adds_train_time(monkeypatch):
    async def fetch(key, keywords, max_price):
        return [L("https://t.jp/far", 1000, "札幌店"), L("https://t.jp/near", 5000, "江坂店")]

    async def commute(a, b):
        return {"江坂": "8 min, direct, ¥190"}.get(b, "no route found")

    monkeypatch.setattr(srv, "fetch_shop", fetch)
    monkeypatch.setattr(srv, "lookup_commute", commute)
    out = await srv.search_shops("x", chains=["trefac"], home_station="吹田")
    assert out["results"][0]["url"] == "https://t.jp/near" and out["results"][0]["why_matches"] == "8 min from 吹田"


async def test_tools_are_registered():
    tools = await srv.server.list_tools()
    assert {t.name for t in tools} == {"search_shops", "commute_time", "market_price", "plan_trip"}
    for t in tools:  # declared for clients: reads public pages only
        a = t.annotations
        assert (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint) == (True, False, True, True)


async def test_search_shops_compares_with_market(monkeypatch):
    async def fetch(key, keywords, max_price):
        return [L("https://h.jp/1", 19800), L("https://h.jp/2", 15300)]

    async def typical(keywords):
        return {"median": 17000, "low": 16000, "high": 18000, "count": 9, "since": "2026-10-20", "source": "x"}

    monkeypatch.setattr(srv, "fetch_shop", fetch)
    monkeypatch.setattr(srv.market, "market_price", typical)
    out = await srv.search_shops("WH-1000XM5", chains=["hardoff"])
    assert out["market"]["median"] == 17000
    assert [r["vs_market_pct"] for r in out["results"]] == [-10, 16]
