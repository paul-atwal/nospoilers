# Bundled team assets

Asset map version: `2026-09-08-nfl32`.

The map covers all 32 active NFL teams using the numeric ESPN team IDs emitted
by `backend/nospoil_nfl/providers/espn_scoreboard.py` as both `id` and
`logoKey` (including Seattle `26` and New England `17`). The source identity
metadata and logo URLs are the ESPN team directory:
`https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams` (retrieved
2026-09-08). Each checked-in PNG was downloaded unchanged from the `logos[0].href`
returned for that team. At retrieval time those URLs followed
`https://a.espncdn.com/i/teamlogos/nfl/500/{team-slug}.png` (for example,
Seattle ID `26` used `sea.png` and New England ID `17` used `ne.png`). The
numeric ID-to-abbreviation list is recorded in `services/teamAssets.ts`. No
source URL is used by the browser.

The checked-in PNGs are third-party team marks and remain subject to their
owners' rights; bundling changes delivery, not ownership. They are used only for
team identification in the same UI that previously loaded them remotely.
Unknown or historical identities fall back to API abbreviation/display text,
and local image-load failures take the same path.
