import asyncio

import httpx
import pytest

from watcher.shop_pages import CHAINS, PARSERS, LayoutChanged, fetch_shop

# trimmed from real result pages (2026-11)
HARDOFF = '''<div class="itemcolmn_item "> <a href="https://netmall.hardoff.co.jp/product/6717299/" >
<div class="item-img-square"> <img onerror="util.alterImage(this, '/x.png')" src="https://p1-d9ebd2ee.imageflux.jp/c!/w=231/201447/a.jpg" alt="ヘッドホン|SONY" /> </div>
<div class="item-infowrap"> <div class="item-brand-name">SONY</div> <div class="item-name">ヘッドホン</div> <div class="item-code">WH-1000XM5</div>
<div class="item-price"> <span class="item-price-icon"> <img src="/assets/images/common/rank/icon-rank-s-b.svg" alt="b"/> </span>
<span class="font-en item-price-en"> 27,500<span class="item-price-jp">円</span> </span> </div> </div> </a> </div>'''

BOOKOFF = '''<div class="productItem__inner"> <a href="/used/0020695349" class="productItem__image js-hoverItemLink">
<img src="https://content.bookoff.co.jp/goodsimages/LL/002069/0020695349LL.jpg" alt="ONE PIECE(巻百十三) ジャンプC" /> </a>
<p class="productItem__title">ONE PIECE(巻百十三) ジャンプC</p> <p class="productItem__price"> &yen;495<span class="productItem__moneyUnit">円</span><small>定価より77円（13%）おトク</small></p>
<p class="productItem__stock"><span class="productItem__stock--alert">在庫あり</span></p></div>
<div class="productItem__inner"> <a href="/used/0099"><img src="/b.jpg" alt="x"/></a> <p class="productItem__title">Gone</p>
<p class="productItem__price"> &yen;100<span>円</span></p> <p class="productItem__stock"><span>在庫なし</span></p></div>'''

TREFAC = '''<li class="pj-search_item"> <a class="cm-itemlist_itemcode_link" href="/item/1029007938733034" >
<div class="cm-itemlist_image"> <img src="/images/1029007938733034/340/a.jpeg" width="340" height="340" alt="SONY (ソニー) ワイヤレスヘッドホン WH-1000XM5 小キズ有"> </div>
<div class="cm-itemlist_price_wrap"> <p class="cm-typo_head4 cm-itemlist_price is-sale">￥16,500</p> </div> </a>
<div class="cm-itemlist_shop_link"> <a href="/search?step=1&shop=34"> <img src="/logo.svg" alt=""> <span>北越谷店</span> </a> </div> </li>
<li class="pj-search_item"> <a class="cm-itemlist_itemcode_link" href="/item/1051007026851626" >
<img class="image-lazyload" data-src="/images/1051007026851626/340/b.jpeg" alt="SONY (ソニー) ワイヤレスヘッドホン WH-1000XM5">
<span class="cm-itemlist_soldout"> <i>SOLD OUT</i> </span> </a> <span>八尾店</span> </li>'''


def test_hardoff():
    [i] = PARSERS["hardoff"](HARDOFF)
    assert i.url == "https://netmall.hardoff.co.jp/product/6717299/"
    assert i.title == "SONY ヘッドホン WH-1000XM5" and i.price == 27500 and i.condition_grade == "B"
    assert i.image_url.startswith("https://p1-d9ebd2ee.imageflux.jp/")


def test_bookoff_skips_out_of_stock():
    [i] = PARSERS["bookoff"](BOOKOFF)
    assert i.url == "https://shopping.bookoff.co.jp/used/0020695349"
    assert i.title == "ONE PIECE(巻百十三) ジャンプC" and i.price == 495


def test_trefac():
    [i] = PARSERS["trefac"](TREFAC)
    assert i.url == "https://ec.treasure-f.com/item/1029007938733034" and i.price == 16500
    assert i.branch == "北越谷店" and "WH-1000XM5" in i.title
    assert i.image_url == "https://ec.treasure-f.com/images/1029007938733034/340/a.jpeg"


def test_fetch_shop_filters_budget_and_handles_errors():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=HARDOFF)

    items = asyncio.run(fetch_shop("hardoff", "WH-1000XM5", 30000, httpx.MockTransport(handler)))
    assert len(items) == 1 and items[0].shop == "Hard Off"
    assert seen == ["https://netmall.hardoff.co.jp/search/?q=WH-1000XM5&exso=1"]
    assert asyncio.run(fetch_shop("hardoff", "x", 20000, httpx.MockTransport(handler))) == []

    blocked = httpx.MockTransport(lambda r: httpx.Response(403, text="no"))
    try:
        asyncio.run(fetch_shop("trefac", "x", None, blocked))
        assert False, "403 must raise so the caller can report the shop as failed"
    except httpx.HTTPStatusError:
        pass


def test_no_results_page_is_empty_not_failure():
    # Hard Off answers an empty search with status 404 and its 'no results' text
    page = httpx.MockTransport(lambda r: httpx.Response(404, text=f"<p>{CHAINS['hardoff'].no_results}</p>"))
    assert asyncio.run(fetch_shop("hardoff", "zqxj", None, page)) == []


def test_unparseable_page_is_layout_change():
    page = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>" + "x" * 5000 + "</html>"))
    with pytest.raises(LayoutChanged):
        asyncio.run(fetch_shop("trefac", "WH-1000XM5", None, page))


def test_hardoff_404_without_marker_still_fails():
    page = httpx.MockTransport(lambda r: httpx.Response(404, text="gone"))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(fetch_shop("hardoff", "x", None, page))


def test_all_out_of_stock_is_empty_not_layout_change():
    sold_out = BOOKOFF.split("<div", 2)[0] + "<div" + BOOKOFF.split("<div", 2)[2]  # only the 在庫なし block
    page = httpx.MockTransport(lambda r: httpx.Response(200, text=sold_out))
    assert asyncio.run(fetch_shop("bookoff", "x", None, page)) == []


# --- pick-up: branch and address from item / branch pages ---
from watcher.models import Listing
from watcher.shop_pages import add_branches, parse_hardoff_item, parse_trefac_shop

HARDOFF_ITEM = '''<script type="application/ld+json">{"offers":{"seller":{"@type":"Organization",
"url":"https://netmall.hardoff.co.jp/shop/201441/","name":"ハードオフ仙台八幡店"}}}</script>
<p class="product-detail-store__address">〒980-0871宮城県仙台市青葉区八幡3-1-50 レキシントンプラザ八幡2F</p>'''
TREFAC_SHOP = '''<th>住所</th> <td class="clearfix"> <p class="add_area"> 〒343-0032<br>埼玉県越谷市袋山1852-1 </p>'''


def test_branch_page_parsers():
    assert parse_hardoff_item(HARDOFF_ITEM) == ("ハードオフ仙台八幡店", "宮城県仙台市青葉区八幡3-1-50")
    assert parse_trefac_shop(TREFAC_SHOP) == "埼玉県越谷市袋山1852-1"
    assert parse_hardoff_item("<html></html>") == (None, None)


def test_trefac_result_links_its_branch_page():
    [i] = PARSERS["trefac"](TREFAC)
    assert i.branch_url == "https://www.treasure-f.com/shop/34/"


def test_add_branches_fills_address_cheapest_first_and_skips_failures():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "6000" in str(req.url):
            return httpx.Response(500)
        return httpx.Response(200, text=TREFAC_SHOP if "treasure-f" in str(req.url) else HARDOFF_ITEM)

    def item(url, price, shop, **kw):
        return Listing(url=url, title="x", price=price, why_matches="", matches=True, verified=False, shop=shop, **kw)

    items = [item("https://netmall.hardoff.co.jp/product/9000/", 9000, "Hard Off"),
             item("https://netmall.hardoff.co.jp/product/1000/", 1000, "Hard Off"),
             item("https://netmall.hardoff.co.jp/product/6000/", 6000, "Hard Off"),
             item("https://ec.treasure-f.com/item/1", 500, "Treasure Factory", branch="北越谷店",
                  branch_url="https://www.treasure-f.com/shop/34/"),
             item("https://ec.treasure-f.com/item/2", 700, "Treasure Factory", branch="北越谷店",
                  branch_url="https://www.treasure-f.com/shop/34/"),
             item("https://shopping.bookoff.co.jp/used/1", 100, "BOOKOFF")]
    out = asyncio.run(add_branches(items, limit=2, transport=httpx.MockTransport(handler)))
    assert [i.url for i in out] == [i.url for i in items]                      # order kept
    assert out[1].branch == "ハードオフ仙台八幡店" and out[1].branch_station == "宮城県仙台市青葉区八幡3-1-50"
    assert out[2].branch_station is None                                        # its page failed
    assert out[0].branch_station is None                                        # beyond limit (2 cheapest)
    assert out[3].branch_station == out[4].branch_station == "埼玉県越谷市袋山1852-1"
    assert seen.count("https://www.treasure-f.com/shop/34/") == 1              # one fetch per branch
    assert not any("bookoff" in u for u in seen)


# --- 駿河屋 (Surugaya) ---
SURUGAYA = '''<div class="item"> <div class="photo_box"> <p class="thum"> <a href="https://www.suruga-ya.jp/product/detail/109101569"><img src="https://www.suruga-ya.jp/database/photo.php?shinaban=109101569&size=m"></a> </p> </div>
<div class="item_detail"> <div class="title"> <a href="https://www.suruga-ya.jp/product/detail/109101569"> <h3 class="product-name">Nintendo Switch本体(有機ELモデル) ホワイト</h3> </a> </div> </div>
<div class="item_price padT12"> <p class="price_teika"> 中古：<span class="text-red"><strong>￥25,800 </strong></span> ～ <span class="text-red"><strong>￥31,000 </strong></span>&nbsp; <span class="tax">税込</span> </p> </div> </div>
<div class="item"> <a href="https://www.suruga-ya.jp/product/detail/1"><h3 class="product-name">sold out one</h3></a>
<div class="item_price padT12"> <p class="price_teika"> 品切れ </p> </div> </div>
<div class="item"> <a href="https://www.suruga-ya.jp/product/detail/2"><h3 class="product-name">new only</h3></a>
<div class="item_price padT12"> <p class="price_teika"> 新品：<span class="text-red"><strong>￥63,980 </strong></span> </p> </div> </div>
<div class="item"> <p class="condition">navigation block without a product link</p> </div>'''


def test_surugaya_keeps_used_in_stock_lowest_price():
    [i] = PARSERS["surugaya"](SURUGAYA)
    assert i.url == "https://www.suruga-ya.jp/product/detail/109101569"
    assert i.title == "Nintendo Switch本体(有機ELモデル) ホワイト" and i.price == 25800
    assert i.image_url.startswith("https://www.suruga-ya.jp/database/photo.php")


def test_fetch_retries_once_after_redirect_page():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:  # first visit sets a cookie and answers with a meta-refresh page
            return httpx.Response(200, text='<meta http-equiv="refresh" content="0;url=x">Redirecting to x',
                                  headers={"set-cookie": "k_uid=abc; Path=/"})
        assert req.headers.get("cookie") == "k_uid=abc"
        return httpx.Response(200, text=SURUGAYA)

    items = asyncio.run(fetch_shop("surugaya", "switch", None, httpx.MockTransport(handler)))
    assert len(calls) == 2 and [i.price for i in items] == [25800] and items[0].shop == "Surugaya"
