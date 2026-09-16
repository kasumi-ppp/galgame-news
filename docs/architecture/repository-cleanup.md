# Repository Cleanup Design

## Goal

Reduce the repository to the Galgame image-prescan v2 implementation and its
verification assets. Keep user DOCX inputs and generated outputs on disk, but
remove them from the Git interface.

## Preserved project surface

The maintained project surface is:

- `src/galgame_news/`: production package and `python -m galgame_news` CLI.
- `tests/`: v2 unit, contract, regression, and integration tests.
- `tests/fixtures/`: deterministic offline regression fixtures.
- `config/default.toml`: versioned default configuration.
- `scripts/`: evaluation and live-smoke helpers.
- `docs/architecture/`: current architecture and this cleanup decision.
- `README.md`, `pyproject.toml`, and `requirements.txt`.

The local `input/` and `output/` directories are preserved without deleting
or moving their contents. They are ignored by Git.

## Removed project surface

The following paths are deleted because the v2 package does not consume them:

- `image_prescan.py`, `legacy/`, `tests/legacy/`, and `docs/legacy/`.
- `agents/` and `docs/superpowers/`, whose implementation briefs are complete.
- Untracked `galnews.py` and `galnews_v2.py`.
- Untracked `config/sources.json` and `config/sources_v2.json`.
- The registered `.superpowers/worktrees/image-prescan-v2` worktree and its
  generated state, cache, and output files. The branch has no commits absent
  from `main`.
- `.idea/`, `.pytest_cache/`, `.ruff_cache/`, `cache/`, root and package
  `__pycache__/` directories, `*.pyc`, and all root `.tmp-*/` directories.

The old tracked `output/259_images_final/` sample is removed from Git. No
other local output is deleted.

## Repository interface

The supported invocation is:

```powershell
python -m galgame_news run INPUT.docx --issue ISSUE --output output/ISSUE
```

`README.md` documents this interface and no longer advertises the legacy
single-file command.

The ignore rules cover:

- Python, pytest, ruff, and IDE caches.
- `.tmp-*/`, `.state/`, `cache/`, and `.superpowers/`.
- `input/` and `output/`.
- Local source-discovery JSON files under `config/sources*.json`.

`config/default.toml` remains tracked.

## Credential handling

The two untracked news-collection scripts contain non-empty hard-coded API-key
literals. They must be deleted without printing or committing their contents.
Deleting the files does not revoke a credential; any real credentials used in
them should be rotated separately.

The maintained v2 implementation reads optional credentials from environment
variables and must not persist credential-bearing URL query parameters in
output indexes.

## Verification

After cleanup:

1. Run `python -m pytest -q`.
2. Run `python -m compileall -q src tests`.
3. Run `git diff --check`.
4. Search tracked files for common credential-assignment patterns without
   printing secret values.
5. Confirm `input/` and `output/` still exist and their file counts are
   unchanged from the pre-cleanup snapshot.
6. Confirm Git status contains only the intended cleanup commit plus the two
   pre-existing user-modified v2 test files.

## Delivery

Commit the cleanup independently on `main`, then push `main` to `origin`.
The push includes the eight existing local commits already ahead of
`origin/main`.
