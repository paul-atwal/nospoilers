# Semi-live excitement ratings

The numeric badge beside `LIVE` measures excitement already observed. It uses
ESPN WP observations joined by `playId` to `drives.previous/current.plays`, never
the newer scoreboard clock or an eventual winner. The existing completed-game
formula and ESPN provisional → nflverse confirmed pipeline remain unchanged.
Live ratings are stored separately and never enter Best of Season rankings.

## Calibration: `live-espn-v1`

`backend/nospoil_nfl/rating/live_calibration.json` was fitted from paired ESPN
prefixes and nflverse completed ratings. It replaces the earlier experimental
nflverse-only stage coefficients. Average absolute WP movement remains an
average; it is not divided by the fraction of the game completed. Observed
recoveries toward 50/50 are weighted by current home/away WP, without assuming
which team will win. Five-minute calibration anchors map the prefix distribution
to training final mean/spread; interpolation is linear between anchors.
Training anchors sample the latest prefix by each nominal game minute. Evaluation
and runtime select the stage from the prefix’s own latest play clock, not the
checkpoint clock.

| Split | Regular-season games | Purpose |
| --- | ---: | --- |
| 2023 | 271 | Fit stage means/spreads and final mean/spread |
| 2024 | 271 | Select stabilization pseudo-count (10) |
| 2025 | 272 | Evaluate the new ESPN adjustment |

The stabilization weight is `n / (n + prior * (1 - regulation_minutes/60)^2)`;
`n` counts WP transitions. Candidates 0, 5, 10, 20, 40 were compared on 2024
checkpoints from minutes 5–15. Selection minimizes distance to final subject to
an early cross-game standard deviation of at least 1.35. This spread constraint
is an explicit heuristic, not a validated optimal watch-quality criterion.
The badge appears only after five elapsed regulation minutes and ten timed WP
transitions. The ESPN pregame baseline lacks play timing and is excluded.

855 historical ESPN games were fetched (2023–2025 regular and postseason).
853 parsed, with 99.45% of their WP observations matched to play clocks. Two
regular-season games were excluded: `2023_02_NO_CAR` (play ordering) and
`2024_04_PHI_TB` (interior timing gap). Unjoined trailing observations wait for
their plays; interior gaps, conflicting duplicates and invalid probabilities
reject a snapshot. Play sequence establishes order; legitimate clock
corrections may add time within a period.

| 2025 regulation checkpoint | Mean absolute distance to nflverse final |
| --- | ---: |
| 15 minutes | 1.891 |
| 30 minutes | 1.630 |
| 45 minutes | 1.267 |
| 55 minutes | 1.050 |
| 59 minutes | 0.864 |

Across consecutive regulation-minute checkpoints, median absolute change was
0.084, p90 0.326, p99 0.824, maximum 4.587. These metrics precede rounding.
Large early jumps remain possible. ESPN completed-prefix ratings themselves
averaged 0.706 points away from nflverse final ratings. Stage adjustment does
not promise prediction accuracy or exact convergence at final. The existing
final formula already used 2016–2025 constants, so 2025 is a holdout for this
new adjustment, not for the original final formula.

## Overtime and finalization

Overtime has an explicit period and clock. It has no assumed duration or
fraction-complete value. The scorer holds the last regulation calibration
anchor while continuing to incorporate **all** overtime WP observations.
Stabilization has already tapered out. This also handles multiple postseason OT
periods without inventing a finish time. Final status immediately hides the
live snapshot; the existing final-only rating and confirmation writers take
over. The existing completed tie adapter is unchanged.

The 2025 replay included 16 overtime games (14 regular season, two postseason),
including one tie. All 296 preterminal overtime prefixes were finite and bounded;
their mean distance to nflverse final was 1.799. This small sample supports the
explicit OT policy, not a separately fitted OT model. Prefix-invariance,
train/evaluation separation and bounded-score assertions passed. The runtime
scorer also matched the replay exactly on 5,137 regulation and 802 overtime
prefixes across all three seasons.

## Freshness, failures and request budget

Each normal live tick runs completed provisional work first, then attempts at
most eight live summaries in batches of four. Oldest attempts (including
failures) go first; never-attempted games precede them. A game is attempted no
more often than every 45 seconds, within the existing 12-hour started-game
recovery window. The Lambda requires 18 seconds remaining
before both a fetch batch and a persistence write. The existing five-second ESPN
summary timeout applies. With 13 simultaneous games, normal healthy ticks
cover all games in roughly two minutes. Source lag and time-budget deferrals
can increase that interval; it is not a latency guarantee.

Historical HTTP response latency was median 0.150s, p95 0.475s, with p95 payload
609 KB. These are local HTTP measurements, **not** a live feed-lag measurement.
Each accepted live rating requires a UTC wallclock on its latest matched play,
no more than ten minutes old or one minute into the future. Expiry is at most
ten minutes after both the source play and calculation. Missing/old timestamps
produce no new rating. A repeated prefix never extends expiry. During halftime
or a long pause, a rating can expire until fresh WP arrives.

Source failures retain the prior snapshot and checkpoint the attempt so one
bad game cannot starve the others. The next due tick retries. DynamoDB compares
status, home/away identity, prior live snapshot and attempt timestamp. A final
or newer-live race returns stale without touching the final rating, mapping,
records or retry fields. Finalization can safely finish after a live write.

Provider-free reads return `liveRating: null` outside `in_progress` or after
expiry. Expiry changes the ETag. The browser also removes expired numbers while
retaining `LIVE`, including during a read outage or cached response. Scores,
winners and completed overtime detail retain their existing reveal behavior.

## Reproduction and model changes

`backend/tools/calibrate_live_ratings.py` accepts `--espn-dir`, `--pbp-dir` and
`--output-dir`; `--download` caches trimmed public ESPN summaries in the supplied
directory. PBP files are the public nflverse `play_by_play_2023.parquet` through
`play_by_play_2025.parquet` releases. It needs NumPy, pandas and PyArrow only for
offline analysis; Lambda dependencies are unchanged. The output includes
`calibration.json`, `results.json` with input SHA-256 hashes, `snapshots.csv` and
`overtime.csv`. Keep downloaded data and local planning artifacts out of Git.
The current cache reproduces the shipped coefficients byte for byte. ESPN can
revise historical feeds; regenerated source hashes identify that change. Bump
the model version when changing coefficients or input semantics.

Sources: [ESPN summary endpoint](https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=401772613),
[nflverse schedule mapping](https://github.com/nflverse/nfldata/blob/master/data/games.csv),
[nflverse PBP releases](https://github.com/nflverse/nflverse-data/releases/tag/pbp).
