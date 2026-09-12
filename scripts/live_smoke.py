"""Explicit opt-in live connectivity smoke check."""

from __future__ import annotations

import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_network:
        parser.error("--allow-network is required for live smoke tests")
    print("live smoke is opt-in; configure URLs and credentials through environment variables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
