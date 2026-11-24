# Thrift Scout

[![M8ven Score](https://m8ven.ai/badge/mcp/tushirsahu-thrift-scout-jknc43)](https://m8ven.ai/mcp/tushirsahu-thrift-scout-jknc43?s=readme)

A Claude Code plugin for buying used goods in Japan. It searches Hard Off, BOOKOFF, Treasure Factory and 駿河屋
(Surugaya), compares prices with recent Yahoo! Auctions sales, and works out train times to the branch that has the
item. It needs no API key: the plugin only fetches and parses public pages, and Claude does the filtering and the
explaining.

![Thrift Scout](assets/banner.png)

## Install

In Claude Code:

```
/plugin marketplace add TushirSahu/thrift-scout
/plugin install thrift-scout@thrift-scout
```

The MCP server is started with `uvx`, so [uv](https://docs.astral.sh/uv/getting-started/installation/) must be
installed. The first start downloads the package (about 80 MB).

## Usage

Ask in English or Japanese:

```
find a used WH-1000XM5 under ¥25,000, I'll pick it up near 吹田
is there a used Switch OLED under ¥30,000? which is the best deal?
what does a used iPad Air 4 usually sell for in Japan?
I need a rice cooker and a microwave this Saturday, I live near 吹田. plan one trip.
```

Example answer for the first request:

```
Typical used price: ¥16,750 (40 auction sales since Nov 7)

1. ¥15,300  Hard Off 江坂店, grade B, 8 min from 吹田       9% below typical
2. ¥19,800  Hard Off 豊中店, grade A, 15 min               18% above typical
3. ¥16,500  Treasure Factory 北越谷店, about 4 h by train   better shipped

Skipped: one WH-1000XM4 (older model), one ジャンク listing.
```

## Tools

| Tool | Description |
|---|---|
| `search_shops` | In-stock items from the four shops within a budget. With `home_station`, looks up each item's branch and sorts by train time. Adds `market` and `vs_market_pct` to the result. |
| `market_price` | Median and middle-half range of recent used sales on Yahoo! Auctions. |
| `commute_time` | Fastest train route between two stations or addresses (Yahoo!乗換案内). |
| `plan_trip` | Given the chosen listings for up to three items, picks a branch for each and the visit order, with time and fare per leg. Branches more than 2 hours away are left out. |

The `find-listings` skill tells Claude how to choose keywords, discard wrong matches (other models, accessories,
ジャンク), label prices, and explain Japanese condition terms and Hard Off's grade letters.

All tools are read-only. Shop and route lookups are cached in the plugin's data folder (30 days; market prices 1 day).

## How it works

Each shop has a small parser that reads its search results page over plain HTTP. If a page loads but contains
neither listings nor the shop's "no results" text, the shop is reported as `page layout changed` rather than
returning an empty result. Market prices come from the JSON embedded in Yahoo! Auctions' closed-listings page;
new, poor-condition, junk and accessory-only sales are excluded before taking the median.

For pick-up, the branch address is read from the item page (Hard Off) or the branch page (Treasure Factory) and
passed to Yahoo!乗換案内, which accepts addresses. `plan_trip` tries every combination of the chosen options and
visit order (at most 48 routes).

## Limitations

- Japan only. Supporting another country means adding its own shops, sold-price source and routing.
- Mercari, Yahoo! Auctions listings, 2nd STREET, じゃんぱら, ソフマップ and GEO are not searched: their pages need a
  browser or block automated requests.
- BOOKOFF and Surugaya sell online from warehouses, so their items have no branch or train time.
- Only the 10 cheapest Hard Off and Treasure Factory items per search get a branch lookup.
- Parsers break when a shop changes its page. A weekly CI job checks the live pages.

## Standalone app

The repository also contains a web page and terminal chat that use a [browser-use](https://github.com/browser-use/browser-use)
agent with your own model key (Gemini, Anthropic, OpenAI or Ollama). They cover the same shops plus Mercari, Yahoo!
Auctions, Rakuma, Amazon.co.jp and SUUMO.

```bash
pip install -e '.[agent]'
cp .env.example .env          # set LLM_PROVIDER, LLM_MODEL and the API key
thrift-scout web              # http://localhost:8000
thrift-scout chat             # terminal
```

The browser agent executes Python on your machine and reads text written by strangers, so it can be targeted by
prompt injection. Run it in Docker (`docker compose up --build`) unless you have a reason not to. Without Docker it is
limited to an allowlist of shopping sites, warns when a page looks like an injection attempt, and has a step budget
(`MAX_STEPS_PER_RUN`). See `.env.example` for all settings.

![Web app demo](assets/demo.gif)

## Development

```bash
pip install -e '.[agent,dev]'
pytest                    # offline tests
pytest -m live            # check the real shop pages still parse
python evals/run.py       # search quality on fixed queries
claude plugin validate .
```

`evals/<case>/` contains end-to-end cases for `claude plugin eval`, with the shop tools replaced by fixed responses.
They check filtering, deal labels, trip plans, failed-shop reporting and a prompt-injection case. Run them from a
fresh clone; each run uses your Claude plan (about $0.10 per case).

## License

MIT. Not affiliated with Hard Off, BOOKOFF, Treasure Factory, Surugaya, Yahoo! JAPAN or LY Corporation. Use it for
personal shopping and follow each site's terms.
