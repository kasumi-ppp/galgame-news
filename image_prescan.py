"""Retired compatibility entry point for the former image prescan tool."""

from __future__ import annotations

import sys


USAGE = "python -m galgame_news run INPUT.docx --issue ISSUE --output output/ISSUE"


def main(_argv: list[str] | None = None) -> int:
    print(
        "image_prescan.py is retired. Use the maintained toolbox entry point:\n"
        f"  {USAGE}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
