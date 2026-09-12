def test_cli_rejects_missing_run_arguments():
    from galgame_news.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "input.docx", "--issue", "1", "--output", "out", "--offline"])
    assert args.offline is True
