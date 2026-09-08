# Bundled team assets

Asset map version: `2026-09-08`.

The two checked-in SVGs are small, local fallback marks derived from the official
team color/name identities (Seattle Seahawks `sea`, New England Patriots `ne`)
for deterministic offline UI tests and demo data. They are not fetched at
runtime. The runtime map is keyed by the API's stable `logoKey`; unknown or
historical keys intentionally fall back to the API abbreviation/display name.

Source verification/download is a release-time concern and is not performed by
the browser.
