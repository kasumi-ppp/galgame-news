# Repository cleanup implementation plan

1. Record the current Git state and input/output file counts.
2. Remove the obsolete registered worktree and generated caches after validating every target is inside this repository.
3. Remove the legacy entry point, legacy implementation/tests/docs, old agent briefs, and obsolete local helper scripts/configuration.
4. Keep `input/` and `output/` contents untouched; remove only the already-deleted tracked sample-output entries from the Git index.
5. Update `.gitignore` and `README.md` for the v2 package and CLI.
6. Run the full test suite, bytecode compilation, whitespace checks, a tracked-file credential scan, and verify input/output counts did not change.
7. Commit only the intended cleanup changes, preserve unrelated user test edits, and push `main` to `origin`.
