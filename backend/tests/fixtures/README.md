# Rating-source fixtures

## ESPN summaries

These are trimmed ESPN summary responses captured on August 24, 2026. They retain only the fields used by the ESPN rating-input normalizer and five representative win-probability entries from each game.

- `espn_summary_regulation.json`: 2025 Week 3, Jets at Buccaneers, event `401772840`.
- `espn_summary_overtime.json`: 2025 Week 12, Giants at Lions, event `401772888`.

The tests read these files locally and never call ESPN.

## nflverse play by play

These are representative rows from the official 2025 nflverse play-by-play CSV release, captured on August 24, 2026. They retain only the fields used by the nflverse rating-input normalizer. The rows are intentionally non-contiguous so the fixtures remain small while covering the start, scoring changes, final score, and overtime where applicable.

- `nflverse_pbp_regulation.json`: 2025 Week 3, Jets at Buccaneers, nflverse game `2025_03_NYJ_TB`.
- `nflverse_pbp_overtime.json`: 2025 Week 12, Giants at Lions, nflverse game `2025_12_NYG_DET`.

The tests load these JSON rows into pandas and never call nflverse.
