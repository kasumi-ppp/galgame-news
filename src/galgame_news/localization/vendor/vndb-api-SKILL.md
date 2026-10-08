---
name: vndb-api
description: This skill provides guidance for making requests to the VNDB (Visual Novel Database) API v2 (Kana). It should be used when querying visual novel information, characters, releases, producers, staff, tags, traits, and managing user lists from VNDB.
---

# VNDB API Skill

This skill helps interact with the VNDB (Visual Novel Database) API v2 (Kana) to query information about visual novels, characters, releases, producers, staff, tags, traits, and user lists.

## API Endpoint

**Base URL**: `https://api.vndb.org/kana`

**Sandbox URL**: `https://beta.vndb.org/api/kana` (for testing)

## Authentication

Most endpoints work without authentication, but user list management requires a token:

1. Obtain a token from VNDB profile → Applications tab (or `https://vndb.org/u/tokens`)
2. Token format: `xxxx-xxxxx-xxxxx-xxxx-xxxxx-xxxxx-xxxx`
3. Include in requests: `Authorization: Token <your-token>`

## Rate Limits

- 200 requests per 5 minutes
- 1 second of execution time per minute
- Requests > 3 seconds are aborted

## Common Data Types

- **vndbid**: Identifier with prefix (e.g., `v17` for visual novels, `c123` for characters, `r456` for releases)
- **release date**: `"YYYY-MM-DD"`, `"YYYY-MM"`, `"YYYY"`, `"TBA"`, `"unknown"`, `"today"`

## Available Endpoints

### Simple Requests (GET)

| Endpoint | Description |
|----------|-------------|
| `GET /schema` | Returns metadata about API objects, enums, and supported external links |
| `GET /stats` | Database statistics (counts of chars, producers, releases, etc.) |
| `GET /user?q=<id>` | Lookup users by ID or username |
| `GET /authinfo` | Validate token and return user info |

### Database Querying (POST)

All POST endpoints accept a JSON query object:

```json
{
  "filters": [],
  "fields": "",
  "sort": "id",
  "reverse": false,
  "results": 10,
  "page": 1
}
```

| Endpoint | Description | Sort Options |
|----------|-------------|--------------|
| `POST /vn` | Visual novels | id, title, released, rating, votecount, searchrank |
| `POST /release` | Releases | id, title, released, searchrank |
| `POST /producer` | Producers/developers | id, name, searchrank |
| `POST /character` | Characters | id, name, searchrank |
| `POST /staff` | Staff/voice actors | id, name, searchrank |
| `POST /tag` | Tags | id, name, vn_count, searchrank |
| `POST /trait` | Character traits | id, name, char_count, searchrank |
| `POST /quote` | Quotes | id, score |

### User List Management

Requires authentication with `listread` or `listwrite` permission.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /ulist` | POST | Fetch user's VN list |
| `GET /ulist_labels` | GET | Fetch user's labels |
| `PATCH /ulist/<id>` | PATCH | Add/update VN in list |
| `PATCH /rlist/<id>` | PATCH | Add/update release in list |
| `DELETE /ulist/<id>` | DELETE | Remove VN from list |
| `DELETE /rlist/<id>` | DELETE | Remove release from list |

## Filter Syntax

Filters use a 3-element array: `["field", "operator", "value"]`

Operators: `=`, `!=`, `>=`, `>`, `<=`, `<`

Combine filters with `and`/`or`:
```json
["and", ["lang", "=", "en"], ["rating", ">", 80]]
```

### Common Filter Flags

- **o**: Supports ordering operators (>, <, etc.)
- **n**: Accepts null as value
- **m**: Single entry can match multiple values
- **i**: Inverting filter is not always equivalent to inverting selection

## Python Script Usage (Recommended)

For a more robust and maintainable solution, use the provided Python script:

### Location
`scripts/vndb_query.py`

### Requirements
- Python 3.7+ (uses only standard library, no external dependencies)

### Quick Commands

```bash
# Search characters
python scripts/vndb_query.py character "美雪"

# Search visual novels
python scripts/vndb_query.py vn "Steins;Gate"

# Get VN by ID
python scripts/vndb_query.py vn_id "v17"

# Get latest released games
python scripts/vndb_query.py latest 5

# Get database stats
python scripts/vndb_query.py stats

# Query user
python scripts/vndb_query.py user "yorhel"
```

### Advanced Usage

```bash
# Custom fields
python scripts/vndb_query.py character "美雪" "name,original,description,vns.title" 10

# Generic endpoint query
python scripts/vndb_query.py query vn '{"search": "悬疑"}' "title,rating" "votecount" 20
```

### Available Commands

| Command | Arguments | Description |
|---------|-----------|-------------|
| `character` | `<keyword>` [fields] [count] | Search characters |
| `vn` | `<keyword>` [fields] [count] | Search visual novels |
| `vn_id` | `<id>` [fields] | Get VN by ID (e.g., v17) |
| `latest` | [count] [fields] | Latest released games |
| `stats` | - | Database statistics |
| `user` | `<username>` [fields] | Query user info |
| `schema` | - | Get API schema |
| `query` | `<endpoint>` `<filters>` `<fields>` [sort] [count] | Generic query |

### Field Reference

**Character fields:** `name,original,image.url,image.thumbnail,description,vns.title,vns.id,traits.name`

**VN fields:** `title,alttitle,image.url,image.thumbnail,rating,votecount,released,developers.name,description,platforms,languages`

**Release fields:** `id,title,alttitle,released,platforms,languages,producers.name,producers.developer,minage`

**Producer fields:** `id,name,original,lang,type,description`

**Staff fields:** `id,name,original,lang,gender,aliases.name`

**Tag fields:** `id,name,aliases,category,description,vn_count`

**Trait fields:** `id,name,aliases,group_name,description,char_count`

See `references/api_docs.md` for complete field documentation.

## curl Example Requests (Alternative)

### Search Visual Novels
```bash
curl https://api.vndb.org/kana/vn \
  --header 'Content-Type: application/json' \
  --data '{
    "filters": ["search", "=", "Steins;Gate"],
    "fields": "title, image.url, rating, description",
    "results": 5
  }'
```

### Get VN by ID
```bash
curl https://api.vndb.org/kana/vn \
  --header 'Content-Type: application/json' \
  --data '{
    "filters": ["id", "=", "v17"],
    "fields": "title, image.url, rating, description, tags.name"
  }'
```

### Get Characters for a VN
```bash
curl https://api.vndb.org/kana/character \
  --header 'Content-Type: application/json' \
  --data '{
    "filters": ["vn", "=", ["id", "=", "v17"]],
    "fields": "name, image.url, description"
  }'
```

### Get User's List (Authenticated)
```bash
curl https://api.vndb.org/kana/ulist \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Token <token>' \
  --data '{
    "user": "u2",
    "fields": "id, vote, vn.title",
    "sort": "vote",
    "reverse": true,
    "results": 10
  }'
```

## Response Format

```json
{
  "results": [...],
  "more": false,
  "count": 100
}
```

- `results`: Array of matching entries
- `more`: True if more results available
- `count`: Total count (only if `"count": true` in request)

## Error Codes

| Code | Meaning |
|------|---------|
| 400 | Invalid request |
| 401 | Invalid token |
| 404 | Invalid path/method |
| 429 | Rate limited |
| 500 | Server error |
| 502 | Server down |

## Tips

- Select only needed fields to avoid "Too much data selected" errors
- Use `id` filtering for efficient pagination instead of `page` parameter
- Batch ID lookups: `["or", ["id", "=", "v1"], ["id", "=", "v2"], ...]`
- Cache schema data - it doesn't change often

## Reference Documentation

For complete API documentation, see `references/api_docs.md` in this skill folder.

