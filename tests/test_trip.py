from watcher import trip
from watcher.trip import Option


def opt(want, branch, address, price, n):
    return Option(want=want, title=f"{want} {n}", url=f"https://shop.jp/{n}", price=price, branch=branch, address=address)


# minutes between places (symmetric), fare ¥100 per 10 min
MIN = {("home", "A"): 10, ("home", "B"): 30, ("home", "C"): 25, ("A", "B"): 25, ("A", "C"): 12, ("B", "C"): 8}


async def leg(a, b):
    m = MIN.get((a, b)) or MIN.get((b, a))
    return f"{m} min, direct, ¥{m * 10}" if m else "no route found"


async def test_picks_fastest_combination_and_order():
    options = [opt("headphones", "Hard Off A", "A", 19800, 1), opt("headphones", "Hard Off B", "B", 15000, 2),
               opt("rice cooker", "Trefac C", "C", 5000, 3), opt("rice cooker", "Trefac B", "B", 4000, 4)]
    plan = await trip.plan_trip("home", options, commute=leg)
    # home->A(10)->C(12)->home(25) = 47 beats any route through B
    assert [s["branch"] for s in plan["stops"]] == ["Hard Off A", "Trefac C"]
    assert [s["leg_minutes"] for s in plan["stops"]] == [10, 12] and plan["return_minutes"] == 25
    assert plan["total_minutes"] == 47 and plan["total_fare"] == 470 and plan["total_price"] == 24800


async def test_one_branch_with_both_items_is_one_stop():
    options = [opt("headphones", "Hard Off B", "B", 15000, 1), opt("rice cooker", "Hard Off B", "B", 4000, 2),
               opt("rice cooker", "Trefac C", "C", 3000, 3)]
    plan = await trip.plan_trip("home", options, commute=leg, round_trip=False)
    assert len(plan["stops"]) == 1 and len(plan["stops"][0]["items"]) == 2 and plan["total_minutes"] == 30


async def test_unreachable_option_is_skipped_and_reported():
    options = [opt("headphones", "Hard Off A", "A", 19800, 1), opt("lamp", "Far away", "Z", 900, 2)]
    plan = await trip.plan_trip("home", options, commute=leg)
    assert [s["branch"] for s in plan["stops"]] == ["Hard Off A"]
    assert plan["skipped"] == ["lamp: no train route found to its branch"]


async def test_limits_options_per_item():
    options = [opt("x", f"b{n}", "A", 100 * n, n) for n in range(5)]
    plan = await trip.plan_trip("home", options, commute=leg)
    assert "at most" in plan["note"]


async def test_far_branches_left_out_and_shipping_suggested():
    options = [opt("headphones", "Far branch", "B", 15000, 1), opt("rice cooker", "Hard Off A", "A", 5000, 2)]
    plan = await trip.plan_trip("home", options, commute=leg, max_minutes=20)
    assert [s["branch"] for s in plan["stops"]] == ["Hard Off A"]
    assert plan["skipped"] == ["headphones: every branch is over 20 min away by train; better to have it shipped"]
