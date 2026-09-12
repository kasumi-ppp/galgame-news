def test_cli_rejects_missing_run_arguments():
    from galgame_news.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "input.docx", "--issue", "1", "--output", "out", "--offline"])
    assert args.offline is True


def test_cli_defaults_output_to_project_output_root():
    from galgame_news.cli import build_parser, DEFAULT_OUTPUT_ROOT

    args = build_parser().parse_args(["run", "input.docx", "--issue", "259", "--offline"])
    assert args.output is None
    assert str(DEFAULT_OUTPUT_ROOT).casefold().endswith(r"e:\project\galgame news\output")
