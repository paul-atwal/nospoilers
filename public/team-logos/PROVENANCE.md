# Bundled team assets

Asset map version: `2026-09-08-nfl32`.

The map covers all 32 active NFL teams using the numeric ESPN team IDs emitted
by `backend/nospoil_nfl/providers/espn_scoreboard.py` as both `id` and
`logoKey` (including Seattle `26` and New England `17`). The source identity
metadata and official color/name reference are the ESPN team directory:
`https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams` (retrieved
2026-09-08), and the corresponding official logo source URL pattern is
`https://a.espncdn.com/i/teamlogos/nfl/500/{id}.png` (identifiers verified
2026-09-08). No source URL is used by the browser.

The checked-in files are intentionally small, original text-mark SVG fallback
assets generated from those verified stable IDs and abbreviations; they are not
downloaded or modified trademark artwork. Every file is local and deterministic.
Unknown or historical identities fall back to API abbreviation/display text,
and image-load failures take the same path.
