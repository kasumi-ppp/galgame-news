# VNDB adapter provenance

## Vendored skill source

This adapter vendors the original `vndb-api` skill assets from
`Gal-criticism/gal-skills`, pinned to revision
`afc24ba678f4e4547027c99284ff8d8f3db37937`:

- [`vendor/vndb-api-SKILL.md`](vendor/vndb-api-SKILL.md), blob SHA
  `decb276ee385fb4752ba451155a682baea81cf64`.
- [`vendor/vndb_query.py`](vendor/vndb_query.py), blob SHA
  `72004c9e091de9aa8189ac891dd596a92ce5f18c`.
- [`vendor/LICENSE-Apache-2.0.txt`](vendor/LICENSE-Apache-2.0.txt), blob SHA
  `261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64`.

The application subclasses the vendored skill's `VNDBClient` and overrides
`_make_request` to use the task's safe HTTP bridge. Only POST `/vn` and
`/release` are permitted; the skill's urllib path, command-line entry point,
and exit-on-error helpers are never executed. The adapter retains the skill's
query client surface while adding a per-news cache, serialized rate control,
bounded query budget, cancellation-aware waits, and structured failures.

## API data contract

The VNDB Kana endpoint is `https://api.vndb.org/kana`. The official API
reference was checked on 2026-10-08 at
https://api.vndb.org/kana. Release records use
`vns{id,title,alttitle,rtype}`; VN screenshots use `url`, `dims`,
`thumbnail`, `thumbnail_dims`, and `release.id`; VN relations use `id`,
`relation`, and `relation_official` with nested VN title fields.

The skill documents a limit of 200 requests per five minutes, one second of
execution time per minute, and requests over three seconds being aborted. The
client serializes calls at a 1.5-second minimum interval, caches API lookups,
and caps each news item at 16 total lookups/pages. A `Retry-After` of five
seconds or less is honored before one retry; longer delays defer the request
and apply a task-wide cooldown. The sync bridge keeps pause, cancellation, URL
safety, response size, and redirect checks active.
