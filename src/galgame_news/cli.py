from __future__ import annotations

import argparse
from pathlib import Path

from .application import Application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="galgame_news")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="process one DOCX issue")
    run.add_argument("input", type=Path)
    run.add_argument("--issue", required=True)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--offline", action="store_true")
    run.add_argument("--config", type=Path)
    run.add_argument("--history-db", type=Path)
    run.add_argument("--llm-provider")
    run.add_argument("--llm-model")
    run.add_argument("--max-images", type=int)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        Application(offline=args.offline, config_path=args.config, history_db=args.history_db, max_images=args.max_images, llm_provider=args.llm_provider, llm_model=args.llm_model).run(args.input, issue_id=args.issue, output_dir=args.output)
    return 0
