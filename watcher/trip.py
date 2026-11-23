"""One train trip to pick up several used items: which branch to get each item from and in what order.

The caller (Claude) chooses the candidate listings; this module only measures train times between their branches
and tries every combination. Train times are looked up once per pair of places and treated as the same both ways."""
import asyncio
import itertools
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from watcher.tools import lookup_commute

MAX_ITEMS, MAX_OPTIONS = 3, 2  # note: brute force over options x visit orders, at most 2^3 x 3! = 48 routes
_LEG = re.compile(r"(\d+) min.*?¥([\d,]+)")


class Option(BaseModel):
    want: str            # what the user asked for, e.g. "headphones"; options with the same want are alternatives
    title: str
    url: str
    price: int | None = None
    branch: str          # e.g. "ハードオフ江坂店"
    address: str         # branch address or station, as search_shops gives it in branch_station


def _leg(text: str) -> tuple[int, int] | None:
    m = _LEG.search(text)
    return (int(m[1]), int(m[2].replace(",", ""))) if m else None


async def plan_trip(home_station: str, options: list[Option], round_trip: bool = True, max_minutes: int = 120,
                    commute: Callable[[str, str], Awaitable[str]] = lookup_commute) -> dict:
    wants: dict[str, list[Option]] = {}
    for o in options:
        wants.setdefault(o.want, []).append(o)
    trimmed = len(wants) > MAX_ITEMS or any(len(v) > MAX_OPTIONS for v in wants.values())
    wants = {w: v[:MAX_OPTIONS] for w, v in list(wants.items())[:MAX_ITEMS]}

    places = sorted({home_station} | {o.address for v in wants.values() for o in v})
    pairs = list(itertools.combinations(places, 2))
    found = await asyncio.gather(*(commute(a, b) for a, b in pairs))
    legs = {}
    for (a, b), text in zip(pairs, found):
        if leg := _leg(text):
            legs[a, b] = legs[b, a] = leg

    skipped = []
    for w in list(wants):
        reachable = [o for o in wants[w] if (home_station, o.address) in legs]
        wants[w] = [o for o in reachable if legs[home_station, o.address][0] <= max_minutes]
        if not reachable:
            skipped.append(f"{w}: no train route found to its branch")
        elif not wants[w]:
            skipped.append(f"{w}: every branch is over {max_minutes} min away by train; better to have it shipped")
        if not wants[w]:
            del wants[w]

    best = None
    for choice in itertools.product(*wants.values()):
        stops = list(dict.fromkeys(o.address for o in choice))
        for order in itertools.permutations(stops):
            path = [home_station, *order] + ([home_station] if round_trip else [])
            steps = [legs.get((a, b)) for a, b in zip(path, path[1:])]
            if None in steps:
                continue
            price = sum(o.price or 0 for o in choice)
            key = (sum(m for m, _ in steps), price)
            if best is None or key < best[0]:
                best = (key, order, choice, steps)

    if best is None:
        return {"stops": [], "skipped": skipped, "note": "No route found through the given branches."}
    (minutes, price), order, choice, steps = best
    stops = [{"branch": next(o.branch for o in choice if o.address == a), "address": a,
              "items": [{"want": o.want, "title": o.title, "price": o.price, "url": o.url}
                        for o in choice if o.address == a],
              "leg_minutes": steps[n][0], "leg_fare": steps[n][1]} for n, a in enumerate(order)]
    plan = {"stops": stops, "total_minutes": minutes, "total_fare": sum(f for _, f in steps), "total_price": price,
            "skipped": skipped,
            "note": "Train times are one-way estimates between places; check the items are still in stock before "
                    "going." + (f" Considered at most {MAX_OPTIONS} options per item and {MAX_ITEMS} items."
                                if trimmed else "")}
    if round_trip:
        plan["return_minutes"], plan["return_fare"] = steps[-1]
    return plan
