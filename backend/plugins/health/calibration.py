"""Compare the readiness score with what the owner logged, and suggest readiness weights.

Each day pairs our four part scores (readiness.py) with up to two references logged by hand:
Apple Fitness's readiness (0-10) and the owner's own 1-5 rating, rescaled to 0-10 as
(rating - 1) * 2.5. The target of the fit is a reference or the mean of both.

Weights are searched on a grid over the simplex (steps of 0.05, all four weights summing to 1)
with the same rule the live score uses: the weighted mean over the parts a day has, needing
`min_components` of them. The best grid point minimises the squared error. Because three weeks
are few data points, the fit is judged by leave-one-out: each day is predicted by the weights
fitted on all other days. A change is only suggested when that leave-one-out error is clearly
smaller than the error of the current weights. Nothing here writes the configuration.
"""

import math
from itertools import product

from plugins.health.observations import CHECKIN_KINDS
from plugins.health.readiness import PARTS

TARGETS = ("both", "apple", "perceived")
GRID_STEPS = 20  # 1 / 20 = 0.05
MIN_DAYS = 21
# The leave-one-out error must be at least this much smaller than with the current weights.
MIN_IMPROVEMENT = 0.10


def scaled(kind, value):
    if value is None:
        return None
    return value if kind == "apple_fitness_readiness" else (value - 1) * 2.5


def target_value(checkin, target):
    apple = scaled("apple_fitness_readiness", checkin.get("apple_fitness_readiness"))
    perceived = scaled("perceived_readiness", checkin.get("perceived_readiness"))
    if target == "apple":
        return apple
    if target == "perceived":
        return perceived
    values = [v for v in (apple, perceived) if v is not None]
    return sum(values) / len(values) if values else None


def records(daily):
    """Completed days of a health_get_daily payload that have both a score and a check-in."""
    found = []
    for day in daily["days"]:
        readiness, checkin = day["readiness"], day.get("checkin") or {}
        if day["in_progress"] or readiness["score"] is None:
            continue
        if all(checkin.get(kind) is None for kind in CHECKIN_KINDS):
            continue
        found.append(
            {
                "date": day["date"],
                "score": readiness["score"],
                "parts": {name: readiness["components"][name]["score"] for name in PARTS},
                "checkin": {kind: checkin.get(kind) for kind in CHECKIN_KINDS},
            }
        )
    return found


def predict(parts, weights, min_components):
    used = [(weights[n], s) for n, s in parts.items() if s is not None and weights[n] > 0]
    total = sum(w for w, _ in used)
    if len(used) < min_components or not total:
        return None
    return sum(w * s for w, s in used) / total


def grid():
    names = list(PARTS)
    for steps in product(range(GRID_STEPS + 1), repeat=len(names) - 1):
        last = GRID_STEPS - sum(steps)
        if last >= 0:
            yield dict(
                zip(names, (*(s / GRID_STEPS for s in steps), last / GRID_STEPS), strict=True)
            )


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            result[order[k]] = (i + j) / 2 + 1  # ties share their average rank
        i = j + 1
    return result


def spearman(xs, ys):
    """Rank correlation, or None with fewer than 3 pairs or no variation."""
    pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (sx * sy)


def normalized(weights):
    total = sum(weights.values())
    return {name: weights[name] / total for name in PARTS}


def distance(a, b):
    return sum(abs(a[n] - b[n]) for n in PARTS)


def calibrate(daily, config, target="both", min_days=MIN_DAYS):
    """Everything the report shows, as plain data; `config` is the plugin's ReadinessConfig."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}")
    rows = [r for r in records(daily) if target_value(r["checkin"], target) is not None]
    current = normalized(config.weights)
    result = {
        "target": target,
        "days": len(rows),
        "min_days": min_days,
        "range": [rows[0]["date"], rows[-1]["date"]] if rows else None,
        "current_weights": {n: round(w, 3) for n, w in current.items()},
        "counts": {
            kind: sum(1 for r in records(daily) if r["checkin"][kind] is not None)
            for kind in CHECKIN_KINDS
        },
    }
    if len(rows) < min_days:
        return {**result, "status": "insufficient"}

    ys = [target_value(r["checkin"], target) for r in rows]
    ours = [r["score"] for r in rows]
    result["correlation"] = {
        "score_vs_target": spearman(ours, ys),
        "score_vs_apple": spearman(
            ours,
            [
                scaled("apple_fitness_readiness", r["checkin"]["apple_fitness_readiness"])
                for r in rows
            ],
        ),
        "score_vs_perceived": spearman(
            ours, [scaled("perceived_readiness", r["checkin"]["perceived_readiness"]) for r in rows]
        ),
        "parts_vs_target": {n: spearman([r["parts"][n] for r in rows], ys) for n in PARTS},
    }
    result["bias"] = sum(o - y for o, y in zip(ours, ys, strict=True)) / len(rows)

    # Every grid point's prediction for every day, once; a candidate that leaves some day
    # without a score (too few weighted parts) is not usable on this data.
    candidates = []
    for weights in grid():
        predictions = [predict(r["parts"], weights, config.min_components) for r in rows]
        if all(p is not None for p in predictions):
            errors = [(p - y) ** 2 for p, y in zip(predictions, ys, strict=True)]
            candidates.append((weights, predictions, errors, sum(errors)))
    if not candidates:
        return {**result, "status": "no_candidates"}

    def best(excluded=None):
        def key(candidate):
            sse = candidate[3] - (candidate[2][excluded] if excluded is not None else 0)
            return (round(sse, 9), distance(candidate[0], current))

        return min(candidates, key=key)

    fitted = best()
    loo = [abs(best(excluded=i)[1][i] - ys[i]) for i in range(len(rows))]
    current_predictions = [predict(r["parts"], config.weights, config.min_components) for r in rows]
    current_mae = sum(abs(p - y) for p, y in zip(current_predictions, ys, strict=True)) / len(rows)
    loo_mae = sum(loo) / len(loo)
    improvement = (current_mae - loo_mae) / current_mae if current_mae else 0.0
    change = improvement >= MIN_IMPROVEMENT and distance(fitted[0], current) > 1e-9
    return {
        **result,
        "status": "suggest_change" if change else "keep",
        "fitted_weights": {n: round(w, 2) for n, w in fitted[0].items()},
        "current_mae": current_mae,
        "fitted_mae": sum(abs(p - y) for p, y in zip(fitted[1], ys, strict=True)) / len(rows),
        "loo_mae": loo_mae,
        "improvement": improvement,
        "min_improvement": MIN_IMPROVEMENT,
    }
