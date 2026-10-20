"""Reproducible paired ESPN → nflverse calibration. No AWS reads or writes."""

from pathlib import Path
import argparse, hashlib, json, sys
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
ROOT = DATA = PBP = None
from backend.nospoil_nfl.providers.espn_live import parse_live_observations
from backend.nospoil_nfl.rating.calculator import calculate_rating
from backend.nospoil_nfl.rating.input import RatingInput
from backend.nospoil_nfl.rating.live_calculator import live_raw_score as raw_score

ANCHORS = list(range(5, 61, 5))


def collect():
    targets = {}
    for season in [2023, 2024, 2025]:
        df = pq.read_table(
            PBP / f"play_by_play_{season}.parquet",
            columns=[
                "game_id",
                "play_id",
                "home_wp",
                "home_score",
                "away_score",
                "desc",
                "season_type",
            ],
        ).to_pandas()
        for gid, g in df.groupby("game_id"):
            g = g.sort_values("play_id", kind="stable")
            scores = g[["home_score", "away_score"]].dropna().drop_duplicates()
            if len(scores) != 1 or not g.desc.fillna("").str.contains("END GAME").any():
                continue
            wp = tuple(g.home_wp.dropna())
            h, a = scores.iloc[0]
            targets[gid] = calculate_rating(RatingInput(wp, bool(h > a)))
    games = []
    failures = []
    raw_count = 0
    aligned_count = 0
    wallclocks = []
    sizes = []
    latencies = []
    for path in sorted(DATA.glob("4*.json")):
        p = json.loads(path.read_text())
        r = p["research"]["game"]
        gid = r["game_id"]
        season = int(r["season"])
        try:
            obs = parse_live_observations(p)
        except Exception as e:
            failures.append({"game_id": gid, "reason": str(e)})
            continue
        if gid not in targets:
            failures.append({"game_id": gid, "reason": "nflverse final unavailable"})
            continue
        raw_count += len(p["winprobability"])
        aligned_count += len(obs)
        latencies.append(p["research"]["latency_seconds"])
        sizes.append(p["research"]["bytes"])
        wp = [x.home_win_probability for x in obs]
        elapsed = np.maximum.accumulate([x.regulation_elapsed_seconds for x in obs])
        period = [x.period for x in obs]
        score = r["home_score"]
        away = r["away_score"]
        espn = calculate_rating(RatingInput(tuple(wp), float(score) > float(away)))
        games.append(
            dict(
                game_id=gid,
                season=season,
                phase=r["game_type"],
                overtime=period[-1] > 4,
                tie=float(score) == float(away),
                target=targets[gid],
                espn_final=espn,
                obs=obs,
                wp=wp,
                elapsed=elapsed,
                period=period,
            )
        )
    return (
        games,
        failures,
        dict(
            raw_wp=raw_count,
            aligned_wp=aligned_count,
            alignment_pct=100 * aligned_count / raw_count,
            latency_p50_seconds=float(np.median(latencies)),
            latency_p95_seconds=float(np.quantile(latencies, 0.95)),
            payload_p95_bytes=float(np.quantile(sizes, 0.95)),
        ),
    )


def snapshot(g, minute):
    indices = [
        i
        for i, x in enumerate(g["elapsed"])
        if x <= minute * 60 and g["period"][i] <= 4
    ]
    if not indices or indices[-1] < 10:
        return None
    n = indices[-1] + 1
    wp = g["wp"][:n]
    return dict(
        game_id=g["game_id"],
        season=g["season"],
        phase=g["phase"],
        minute=minute,
        stage_minute=float(g["elapsed"][n - 1]) / 60,
        n=n - 1,
        raw=raw_score(wp),
        target=g["target"],
        overtime=g["overtime"],
    )


def score(raw, n, minute, c, prior):
    m = np.minimum(minute, 60)
    mean = np.interp(m, c["minutes"], c["means"])
    std = np.interp(m, c["minutes"], c["stds"])
    w = n / (n + prior * (1 - m / 60) ** 2)
    return np.clip(c["target_mean"] + w * ((raw - mean) / std * c["target_std"]), 0, 10)


def metrics(a, b):
    a = np.array(a)
    b = np.array(b)
    if not len(a):
        return dict(n=0)
    return dict(
        n=len(a),
        mean=float(a.mean()),
        std=float(a.std()),
        mae_to_final=float(abs(a - b).mean()),
        p90_distance=float(np.quantile(abs(a - b), 0.9)),
        clipped_pct=float(np.mean((a == 0) | (a == 10)) * 100),
    )


def fit():
    games, failures, coverage = collect()
    rows = [r for g in games for m in range(5, 61) if (r := snapshot(g, m))]
    frame = pd.DataFrame(rows)
    train = frame[(frame.season == 2023) & (frame.phase == "REG")]
    targets = [
        g["target"] for g in games if g["season"] == 2023 and g["phase"] == "REG"
    ]
    c = dict(
        minutes=ANCHORS,
        means=[float(train[train.minute == m].raw.mean()) for m in ANCHORS],
        stds=[float(train[train.minute == m].raw.std(ddof=0)) for m in ANCHORS],
        target_mean=float(np.mean(targets)),
        target_std=float(np.std(targets)),
        train_seasons=[2023],
        validation_seasons=[2024],
        holdout_seasons=[2025],
        source="espn",
        model_version="live-espn-v1",
    )
    validation = frame[
        (frame.season == 2024)
        & (frame.phase == "REG")
        & (frame.minute <= 15)
        & (frame.stage_minute >= 5)
    ]
    candidates = []
    for prior in [0, 5, 10, 20, 40]:
        s = score(validation.raw, validation.n, validation.stage_minute, c, prior)
        candidates.append(dict(prior=prior, **metrics(s, validation.target)))
    eligible = [x for x in candidates if x["std"] >= 1.35]
    prior = min(eligible, key=lambda x: x["mae_to_final"])["prior"]
    c["prior"] = prior
    frame["rating"] = score(frame.raw, frame.n, frame.stage_minute, c, prior)
    shown = frame[frame.stage_minute >= 5]
    hold = shown[(shown.season == 2025) & (shown.phase == "REG")]
    holdgames = [g for g in games if g["season"] == 2025 and g["phase"] == "REG"]
    ot = []
    for g in games:
        if g["season"] != 2025 or not g["overtime"]:
            continue
        for i, x in enumerate(g["obs"][:-1]):
            if x.period <= 4:
                continue
            wp = g["wp"][: i + 1]
            raw = raw_score(wp)
            s = float(score(raw, i, 60, c, prior))
            ot.append(
                dict(
                    game_id=g["game_id"],
                    period=x.period,
                    clock=x.clock,
                    tie=g["tie"],
                    rating=s,
                    target=g["target"],
                )
            )
    changes = (
        hold.sort_values(["game_id", "minute"])
        .groupby("game_id")
        .rating.diff()
        .abs()
        .dropna()
    )
    finaldiff = [g["espn_final"] - g["target"] for g in holdgames]
    for g in games[::17]:
        for minute in [5, 15, 30, 45, 59]:
            orig = snapshot(g, minute)
            new = dict(g, wp=list(g["wp"]))
            n = (
                max(
                    [
                        i
                        for i, x in enumerate(g["elapsed"])
                        if x <= minute * 60 and g["period"][i] <= 4
                    ],
                    default=-1,
                )
                + 1
            )
            new["wp"][n:] = [1 - x for x in new["wp"][n:]]
            assert snapshot(new, minute) == orig
    assert set(train.game_id).isdisjoint(set(hold.game_id))
    result = dict(
        coverage=coverage,
        excluded=failures,
        games_by_split={
            str(s): len([g for g in games if g["season"] == s and g["phase"] == "REG"])
            for s in [2023, 2024, 2025]
        },
        validation_candidates=candidates,
        selected_prior=prior,
        holdout={
            str(m): metrics(
                hold[hold.minute == m].rating, hold[hold.minute == m].target
            )
            for m in [5, 15, 30, 45, 55, 59, 60]
        },
        source_final_mae=float(np.mean(np.abs(finaldiff))),
        minute_change_quantiles={
            str(q): float(changes.quantile(q)) for q in [0.5, 0.9, 0.99, 1]
        },
        ot_games=len(set(x["game_id"] for x in ot)),
        ot_ties=len(set(x["game_id"] for x in ot if x["tie"])),
        ot_prefixes=metrics([x["rating"] for x in ot], [x["target"] for x in ot]),
        checks=[
            "future suffix invariance",
            "train/holdout separation",
            "all scores finite and bounded",
        ],
        source_sha256={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in DATA.glob("4*.json")
        },
    )
    assert np.all(np.isfinite(frame.rating)) and frame.rating.between(0, 10).all()
    (ROOT / "calibration.json").write_text(json.dumps(c, indent=2) + "\n")
    (ROOT / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    shown.to_csv(ROOT / "snapshots.csv", index=False)
    pd.DataFrame(ot).to_csv(ROOT / "overtime.csv", index=False)
    print(
        json.dumps({k: v for k, v in result.items() if k != "source_sha256"}, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--espn-dir",
        required=True,
        type=Path,
        help="ESPN summary cache produced by --download",
    )
    parser.add_argument(
        "--pbp-dir",
        required=True,
        type=Path,
        help="nflverse play_by_play_2023/2024/2025.parquet directory",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--download",
        action="store_true",
        help="Read public historical ESPN summaries, caching trimmed JSON",
    )
    args = parser.parse_args()
    ROOT = args.output_dir
    DATA = args.espn_dir
    PBP = args.pbp_dir
    ROOT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    if args.download:
        import concurrent.futures, csv, io, requests

        source = requests.get(
            "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
            timeout=30,
        )
        source.raise_for_status()
        rows = [
            r
            for r in csv.DictReader(io.StringIO(source.text))
            if r["season"] in {"2023", "2024", "2025"} and r["espn"] and r["home_score"]
        ]

        def download(r):
            import time

            path = DATA / f"{r['espn']}.json"
            if path.exists():
                return
            started = time.monotonic()
            response = requests.get(
                "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
                params={"event": r["espn"]},
                timeout=15,
            )
            response.raise_for_status()
            p = response.json()
            drives = p.get("drives", {})
            ds = drives.get("previous", []) + (
                [drives["current"]] if drives.get("current") else []
            )
            # Keep every source play, including duplicates, so the shared parser can
            # detect conflicts. Responses omit unrelated news, media and box scores.
            plays = [
                {
                    k: x[k]
                    for k in ["id", "sequenceNumber", "period", "clock", "wallclock"]
                    if k in x
                }
                for d in ds
                for x in d.get("plays", [])
            ]
            small = {
                "header": p.get("header"),
                "winprobability": p.get("winprobability", []),
                "drives": {"previous": [{"plays": plays}]},
                "research": {
                    "game": r,
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "bytes": len(response.content),
                },
            }
            path.write_text(json.dumps(small, separators=(",", ":")))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(download, rows))
    fit()
