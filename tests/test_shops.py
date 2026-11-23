import asyncio

from watcher.models import Listing
from watcher.shops import CHAINS, Plan, build_graph, current_chain, rank, run_shop_search


def L(url, price, grade=None, station=None, shop=None):
    return Listing(url=url, title=f"item {price}", price=price, why_matches="fits", matches=True, verified=True,
                   condition_grade=grade, branch_station=station, shop=shop)


def test_chains_have_search_urls_and_site_patterns():
    assert set(CHAINS) == {"hardoff", "bookoff", "trefac", "surugaya"}
    assert CHAINS["hardoff"].search_url("WH-1000XM5") == "https://netmall.hardoff.co.jp/search/?q=WH-1000XM5&exso=1"
    assert CHAINS["bookoff"].search_url("ソニー ヘッドホン").startswith("https://shopping.bookoff.co.jp/search/keyword/")
    assert all(c.site.startswith("*.") for c in CHAINS.values())


def test_rank_ship_by_price_then_grade():
    plan = Plan(keywords="x", mode="ship")
    out = asyncio.run(rank(plan, [L("https://a.jp/1", 9000, "B"), L("https://a.jp/2", 8000, "C"), L("https://a.jp/3", 8000, "A"),
                      L("https://a.jp/4", None)], commute=None))
    assert [i.url for i in out] == ["https://a.jp/3", "https://a.jp/2", "https://a.jp/1", "https://a.jp/4"]


def test_rank_pickup_by_train_time():
    plan = Plan(keywords="x", mode="pickup", home_station="吹田")
    times = {"梅田": "15 min, direct, ¥230", "京都": "45 min, 1 transfer, ¥580"}
    asked = []

    async def commute(a, b):
        asked.append((a, b))
        return times.get(b, "no route found")

    items = [L("https://a.jp/kyoto", 5000, station="京都"), L("https://a.jp/umeda", 9000, station="梅田"),
             L("https://a.jp/umeda2", 7000, station="梅田"), L("https://a.jp/none", 1000)]
    out = asyncio.run(rank(plan, items, commute=commute))
    assert [i.url for i in out] == ["https://a.jp/umeda2", "https://a.jp/umeda", "https://a.jp/kyoto", "https://a.jp/none"]
    assert "15 min from 吹田" in out[0].why_matches
    assert sorted(asked) == [("吹田", "京都"), ("吹田", "梅田")]  # one lookup per branch station


def test_graph_runs_chains_in_parallel_and_merges():
    started, streamed = [], []
    plan = Plan(keywords="WH-1000XM5", max_price=25000, mode="ship", chains=["hardoff", "bookoff", "trefac"])

    async def plan_fn(text):
        return plan

    async def search_fn(chain, p, on_listing):
        started.append(chain)
        assert current_chain.get() == CHAINS[chain].name   # step lines can be tagged per chain
        await asyncio.sleep(0.05)
        item = L(f"https://{chain}.jp/1", {"hardoff": 9000, "bookoff": 7000, "trefac": 8000}[chain])
        on_listing(item)
        return [item, L("javascript:void(0)", 1)]  # junk link must be cleaned

    reply = asyncio.run(run_shop_search("find xm5", plan_fn=plan_fn, search_fn=search_fn, commute=None,
                                        on_listing=streamed.append))
    assert sorted(started) == ["bookoff", "hardoff", "trefac"]
    assert [i.price for i in reply.listings] == [7000, 8000, 9000]
    assert {i.shop for i in reply.listings} == {"BOOKOFF", "Hard Off", "Treasure Factory"}
    assert len(streamed) == 3
    assert "3 shops" in reply.answer and "price" in reply.answer


def test_graph_survives_one_chain_failing():
    async def plan_fn(text):
        return Plan(keywords="x", chains=["hardoff", "trefac"])

    async def search_fn(chain, p, on_listing):
        if chain == "trefac":
            raise RuntimeError("blocked")
        return [L("https://h.jp/1", 5000)]

    reply = asyncio.run(run_shop_search("x", plan_fn=plan_fn, search_fn=search_fn, commute=None))
    assert len(reply.listings) == 1 and "Treasure Factory" in reply.answer and "couldn't" in reply.answer


def test_not_a_shop_request_returns_none():
    async def plan_fn(text):
        return Plan(keywords="x", target="marketplaces")

    async def search_fn(*a):
        raise AssertionError("must not search")

    assert asyncio.run(run_shop_search("x", plan_fn=plan_fn, search_fn=search_fn, commute=None)) is None


def test_build_graph_compiles():
    g = build_graph(plan_fn=None, search_fn=None, commute=None)
    assert {"plan", "search", "merge", "rank"} <= set(g.get_graph().nodes)


# --- real planner / per-chain search wiring (no network: pydantic-ai TestModel, fake ChatSession) ---
import json

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import watcher.shops as shops_mod
from watcher.models import Reply


class FakeLLM:
    """Planner returns plan_args; the judge returns picks (list of {n, why}) and records what it was shown."""
    def __init__(self, plan_args, picks=()):
        self.plan_args, self.picks, self.judged = plan_args, list(picks), []

    def pydantic_model(self):
        def reply(messages, info: AgentInfo):
            tool = info.output_tools[0]
            if "keywords" in json.dumps(tool.parameters_json_schema):
                return ModelResponse(parts=[ToolCallPart(tool.name, self.plan_args)])
            self.judged.append(messages[-1].parts[-1].content)
            return ModelResponse(parts=[ToolCallPart(tool.name, {"picks": self.picks})])
        return FunctionModel(reply)


def no_parser(monkeypatch):
    async def fail(*a, **k):
        raise RuntimeError("page changed")
    monkeypatch.setattr(shops_mod, "fetch_shop", fail)


def test_make_shop_search_falls_back_to_browser(monkeypatch):
    no_parser(monkeypatch)
    sessions = []

    class FakeSession:
        def __init__(self, llm, **kw):
            self.kw, self.closed, self.asked = kw, 0, []
            sessions.append(self)

        async def ask(self, text):
            self.asked.append(text)
            item = L(f"https://{self.kw['allowed_sites'][0][2:]}/item/1", 5000, "A", "梅田")
            self.kw["on_listing"](item)
            return Reply(answer="ok", listings=[item])

        async def close(self):
            self.closed += 1

    monkeypatch.setattr(shops_mod, "ChatSession", FakeSession)

    async def commute(a, b):
        return "12 min, direct, ¥200"

    monkeypatch.setattr(shops_mod, "lookup_commute", commute)
    llm = FakeLLM({"target": "shops", "keywords": "WH-1000XM5", "max_price": 25000, "mode": "pickup",
                   "home_station": "吹田", "chains": ["hardoff", "bookoff"]})
    search = shops_mod.make_shop_search(llm, {"headless": True})
    streamed = []
    reply = asyncio.run(search("xm5 near 吹田", streamed.append))
    assert {s.kw["allowed_sites"][0] for s in sessions} == {"*.hardoff.co.jp", "*.bookoff.co.jp"}
    hard = next(s for s in sessions if s.kw["allowed_sites"] == ["*.hardoff.co.jp"])
    task = hard.asked[0]
    assert "https://netmall.hardoff.co.jp/search/?q=WH-1000XM5&exso=1" in task and "¥25,000" in task
    assert "branch_station" in task and "condition_grade" in task
    assert all(s.closed == 1 for s in sessions)
    assert len(reply.listings) == 2 and "12 min from 吹田" in reply.listings[0].why_matches
    assert len(streamed) == 2 and streamed[0].shop in {"Hard Off", "BOOKOFF"}


def test_make_shop_search_error_turn_counts_as_failed(monkeypatch):
    no_parser(monkeypatch)
    class ErrSession:
        def __init__(self, llm, **kw): pass
        async def ask(self, text): return Reply(answer="error: blocked")
        async def close(self): pass

    monkeypatch.setattr(shops_mod, "ChatSession", ErrSession)
    search = shops_mod.make_shop_search(FakeLLM({"keywords": "x", "chains": ["bookoff"]}), {})
    reply = asyncio.run(search("x", lambda i: None))
    assert reply.listings == [] and "couldn't search BOOKOFF" in reply.answer


def test_marketplace_request_returns_none(monkeypatch):
    search = shops_mod.make_shop_search(FakeLLM({"target": "marketplaces", "keywords": "x"}), {})
    assert asyncio.run(search("xm5 on mercari", lambda i: None)) is None


def test_branch_place_from_branch_name():
    from watcher.shop_pages import branch_place
    assert branch_place("ハードオフ白岡店") == "白岡"
    assert branch_place("ホビーオフ足立南花畑店") == "足立南花畑"
    assert branch_place("BOOKOFF 江坂店") == "江坂"
    assert branch_place(None) is None


def test_rank_pickup_uses_branch_name_when_station_missing():
    plan = Plan(keywords="x", mode="pickup", home_station="吹田")

    async def commute(a, b):
        return {"江坂": "8 min, direct, ¥190"}.get(b, "no route found")

    items = [L("https://a.jp/far", 1000), L("https://a.jp/near", 9000)]
    items[0] = items[0].model_copy(update={"branch": "ハードオフ札幌北都店"})
    items[1] = items[1].model_copy(update={"branch": "ハードオフ江坂店"})
    out = asyncio.run(rank(plan, items, commute=commute))
    assert out[0].url == "https://a.jp/near" and "8 min from 吹田" in out[0].why_matches


def test_answer_does_not_claim_train_time_when_none_found():
    async def plan_fn(text):
        return Plan(keywords="x", mode="pickup", home_station="吹田", chains=["hardoff"])

    async def search_fn(chain, p, on_listing):
        return [L("https://h.jp/1", 5000)]

    async def commute(a, b):
        return "no route found"

    reply = asyncio.run(run_shop_search("x", plan_fn=plan_fn, search_fn=search_fn, commute=commute))
    assert "train time" not in reply.answer.split(".")[0] and "couldn't find train times" in reply.answer


def test_parser_first_judge_picks_and_no_browser(monkeypatch):
    class NoBrowser:
        def __init__(self, *a, **k):
            raise AssertionError("parser worked, browser must not start")

    async def fetch(key, keywords, max_price):
        return [L(f"https://{key}.jp/game", 4000), L(f"https://{key}.jp/console", 30000),
                L(f"https://{key}.jp/console2", 28000)]

    async def branches(items):
        return [i.model_copy(update={"branch_station": "大阪府吹田市1-1"}) for i in items]

    async def commute(a, b):
        return "20 min, direct, ¥200"

    monkeypatch.setattr(shops_mod, "ChatSession", NoBrowser)
    monkeypatch.setattr(shops_mod, "fetch_shop", fetch)
    monkeypatch.setattr(shops_mod, "add_branches", branches)
    monkeypatch.setattr(shops_mod, "lookup_commute", commute)
    llm = FakeLLM({"keywords": "PS5", "max_price": 35000, "mode": "pickup", "home_station": "江坂",
                   "chains": ["hardoff"]}, picks=[{"n": 3, "why": "本体"}, {"n": 2, "why": "本体"}, {"n": 99, "why": "x"}])
    streamed = []
    reply = asyncio.run(shops_mod.make_shop_search(llm, {})("PS5本体 near 江坂, no games", streamed.append))
    assert [i.url for i in reply.listings] == ["https://hardoff.jp/console2", "https://hardoff.jp/console"]
    assert reply.listings[0].why_matches.startswith("本体") and "20 min from 江坂" in reply.listings[0].why_matches
    assert "PS5本体 near 江坂, no games" in llm.judged[0] and "1. ¥4000" in llm.judged[0]   # judge sees request + numbered titles
    assert len(streamed) == 2

