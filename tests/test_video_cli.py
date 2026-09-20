def test_cli_exposes_no_videos_switch():
    from galgame_news.cli import build_parser

    args = build_parser().parse_args(
        ["run", "input.docx", "--issue", "1", "--no-videos"]
    )
    assert args.no_videos is True
