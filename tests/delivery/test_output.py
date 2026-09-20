from datetime import datetime, timezone
import json

from galgame_news.domain import ImageCandidate, ImageNeed, ImageType, Issue, NewsItem, PipelineResult, ScoreBreakdown, SourceType


def test_output_manager_writes_indexes_and_news_mapping(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="259", sequence=1, section="新作", title="《Game》更新", body="", image_need=ImageNeed.UNKNOWN)
    issue = Issue(issue_id="259", input_path="fixture.docx", news_items=[item])
    candidate = ImageCandidate(news_id=item.id, image_url="https://cdn.example/a.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), selected=True)
    manifest = OutputManager().write(PipelineResult(issue=issue, candidates=[candidate]), tmp_path)
    assert manifest.image_count == 1
    for name in ("image_index.json", "image_index.md", "failed_items.json", "review_required.json"):
        assert (tmp_path / name).exists()
    payload = json.loads((tmp_path / "image_index.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["news_items"][0]["news_id"] == item.id


def test_output_is_repeatable_and_does_not_duplicate_candidate_files(tmp_path):
    from galgame_news.delivery.output import OutputManager

    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[])
    result = PipelineResult(issue=issue)
    manager = OutputManager()
    manager.write(result, tmp_path)
    manager.write(result, tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 4


def test_output_uses_human_readable_section_sequence_names(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="1", sequence=2, section="新作", title="《Game》更新", body="", image_need=ImageNeed.UNKNOWN)
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])
    source = tmp_path / "source.jpg"
    source.write_bytes(b"sample")
    candidate = ImageCandidate(news_id=item.id, image_url="https://cdn.example/a.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), local_path=str(source), selected=True)
    OutputManager().write(PipelineResult(issue=issue, candidates=[candidate]), tmp_path / "out")
    assert (tmp_path / "out" / "images" / "x1" / "x1.01.jpg").exists()


def test_output_numbers_each_known_section_independently(tmp_path):
    from galgame_news.delivery.output import OutputManager

    items = [
        NewsItem(issue_id="1", sequence=1, section="新作", title="X1", body=""),
        NewsItem(issue_id="1", sequence=2, section="新作", title="X2", body=""),
        NewsItem(issue_id="1", sequence=3, section="汉化", title="H1", body=""),
        NewsItem(issue_id="1", sequence=4, section="周边", title="Z1", body=""),
    ]
    source = tmp_path / "source.jpg"
    source.write_bytes(b"sample")
    candidates = [
        ImageCandidate(
            news_id=item.id,
            image_url=f"https://cdn.example/{item.sequence}.jpg",
            source_url="https://official.example",
            source_type=SourceType.OFFICIAL_SITE,
            fetched_at=datetime.now(timezone.utc),
            local_path=str(source),
            selected=True,
        )
        for item in items
    ]
    OutputManager().write(PipelineResult(issue=Issue(issue_id="1", input_path="fixture.docx", news_items=items), candidates=candidates), tmp_path / "out")
    expected = {
        "x1/x1.01.jpg",
        "x2/x2.01.jpg",
        "h1/h1.01.jpg",
        "z1/z1.01.jpg",
    }
    actual = {path.relative_to(tmp_path / "out" / "images").as_posix() for path in (tmp_path / "out" / "images").rglob("*.jpg")}
    assert actual == expected


def test_output_accepts_chinese_section_aliases_and_numbers_by_category_appearance(tmp_path):
    from galgame_news.delivery.output import OutputManager

    # ``sequence`` is intentionally not sorted here: names must follow the
    # order in which each category appears in the issue, not the global number.
    items = [
        NewsItem(issue_id="1", sequence=8, section="漢化情報", title="H first", body=""),
        NewsItem(issue_id="1", sequence=2, section="新作", title="X first", body=""),
        NewsItem(issue_id="1", sequence=3, section="漢化", title="H second", body=""),
        NewsItem(issue_id="1", sequence=1, section="周邊", title="Z first", body=""),
        NewsItem(issue_id="1", sequence=4, section="業界", title="Z second", body=""),
    ]
    source = tmp_path / "source.jpg"
    source.write_bytes(b"sample")
    candidates = [
        ImageCandidate(
            news_id=item.id,
            image_url=f"https://cdn.example/{item.sequence}.jpg",
            source_url="https://official.example",
            source_type=SourceType.OFFICIAL_SITE,
            fetched_at=datetime.now(timezone.utc),
            local_path=str(source),
            selected=True,
        )
        for item in items
    ]

    OutputManager().write(
        PipelineResult(
            issue=Issue(issue_id="1", input_path="fixture.docx", news_items=items),
            candidates=candidates,
        ),
        tmp_path / "out",
    )

    image_root = tmp_path / "out" / "images"
    assert {
        path.relative_to(image_root).as_posix()
        for path in image_root.rglob("*.jpg")
    } == {
        "h1/h1.01.jpg",
        "x1/x1.01.jpg",
        "h2/h2.01.jpg",
        "z1/z1.01.jpg",
        "z2/z2.01.jpg",
    }


def test_output_rerun_removes_managed_images_that_are_no_longer_present(tmp_path):
    from galgame_news.delivery.output import OutputManager
    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Game", body="", image_need=ImageNeed.UNKNOWN)
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])
    source = tmp_path / "source.jpg"; source.write_bytes(b"sample")
    candidates = [ImageCandidate(news_id=item.id, image_url=f"https://cdn.example/{i}.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), local_path=str(source), selected=True) for i in range(10)]
    manager = OutputManager(); out = tmp_path / "out"
    manager.write(PipelineResult(issue=issue, candidates=candidates), out)
    assert len(list((out / "images" / "x1").glob("*.jpg"))) == 10
    source2 = tmp_path / "source2.jpg"; source2.write_bytes(b"sample2")
    second = [ImageCandidate(news_id=item.id, image_url=f"https://cdn.example/second-{i}.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), local_path=str(source2), selected=True) for i in range(3)]
    manager.write(PipelineResult(issue=issue, candidates=second), out)
    assert sorted(p.name for p in (out / "images" / "x1").glob("*.jpg")) == ["x1.01.jpg", "x1.02.jpg", "x1.03.jpg"]


def test_output_redacts_signed_url_credentials_from_json_indexes(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="254", sequence=1, section="周边", title="OST发售", body="")
    signed_url = (
        "https://cdn.example/cover.png?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
        "X-Amz-Credential=EXAMPLEACCESS%2F20260915%2Fregion%2Fs3%2Faws4_request&"
        "X-Amz-Signature=secret-signature&safe=value"
    )
    candidate = ImageCandidate(
        news_id=item.id,
        image_url=signed_url,
        source_url="https://official.example/ost",
        source_type=SourceType.OFFICIAL_SITE,
        fetched_at=datetime.now(timezone.utc),
        review_reasons=["unknown_publish_time"],
        selected=False,
    )
    out = tmp_path / "out"

    OutputManager().write(PipelineResult(issue=Issue(issue_id="254", input_path="254.docx", news_items=[item]), candidates=[candidate]), out)

    for name in ("image_index.json", "review_required.json"):
        text = (out / name).read_text(encoding="utf-8")
        assert "X-Amz-Credential" not in text
        assert "X-Amz-Signature" not in text
        assert "EXAMPLEACCESS" not in text
        assert "safe=value" in text


def _score(relevance: float, total: float) -> ScoreBreakdown:
    return ScoreBreakdown(
        relevance=relevance,
        freshness=0.5,
        source_trust=0.8,
        quality=0.9,
        type_match=0.9,
        total=total,
    )


def test_output_exports_reviewable_failures_by_relevance_and_total(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="1", sequence=1, section="新作", title="《Game》更新", body="")
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])

    def candidate(name, image_type, *, relevance, total, width=1280, height=720, selected=False):
        source = tmp_path / f"{name}.jpg"
        source.write_bytes(name.encode("ascii"))
        return ImageCandidate(
            news_id=item.id,
            image_url=f"https://cdn.example/{name}.jpg",
            source_url="https://official.example/game",
            source_type=SourceType.OFFICIAL_SITE,
            image_type=image_type,
            image_type_confidence=0.9,
            fetched_at=datetime.now(timezone.utc),
            width=width,
            height=height,
            mime_type="image/jpeg",
            downloadable=True,
            local_path=str(source),
            score=_score(relevance, total),
            selected=selected,
            signals={"entity_match": True, "entity_match_confidence": 0.95},
        )

    selected = candidate("selected", ImageType.GAME_CG, relevance=1.0, total=99, selected=True)
    cg_lower = candidate("cg-lower", ImageType.GAME_CG, relevance=0.70, total=70)
    screenshot = candidate("screenshot", ImageType.GAMEPLAY_SCREENSHOT, relevance=0.99, total=98)
    cg_higher = candidate("cg-higher", ImageType.GAME_CG, relevance=0.90, total=90)
    logo = candidate("logo", ImageType.LOGO, relevance=1.0, total=100)
    low_resolution = candidate("low-resolution", ImageType.GAME_CG, relevance=1.0, total=100, width=240, height=180)

    out = tmp_path / "out"
    OutputManager().write(
        PipelineResult(
            issue=issue,
            candidates=[selected, cg_lower, screenshot, cg_higher, logo, low_resolution],
        ),
        out,
    )

    assert (out / "images" / "x1" / "x1.01.jpg").read_bytes() == b"selected"
    failure_dir = out / "images" / "x1" / "未候选"
    assert [path.name for path in failure_dir.glob("*.jpg")] == [
        "x1.u01.jpg",
        "x1.u02.jpg",
        "x1.u03.jpg",
    ]
    assert [(failure_dir / f"x1.u0{rank}.jpg").read_bytes() for rank in range(1, 4)] == [
        b"screenshot",
        b"cg-higher",
        b"cg-lower",
    ]
    assert logo.local_path is None
    assert low_resolution.local_path is None
    assert cg_higher.selected is False
    assert "未候选" in (cg_higher.local_path or "")

    payload = json.loads((out / "image_index.json").read_text(encoding="utf-8"))
    by_id = {candidate["id"]: candidate for candidate in payload["candidates"]}
    assert by_id[cg_higher.id]["selected"] is False
    assert "未候选" in by_id[cg_higher.id]["local_path"]


def test_output_deduplicates_failed_candidates_by_content_hash(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="1", sequence=1, section="新作", title="《Game》更新", body="")
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])
    source = tmp_path / "same.jpg"
    source.write_bytes(b"same")
    candidates = [
        ImageCandidate(
            news_id=item.id,
            image_url=f"https://cdn.example/{index}.jpg",
            source_url="https://official.example/game",
            source_type=SourceType.OFFICIAL_SITE,
            image_type=ImageType.GAME_CG,
            fetched_at=datetime.now(timezone.utc),
            width=1280,
            height=720,
            mime_type="image/jpeg",
            sha256="same-sha256",
            downloadable=True,
            local_path=str(source),
            score=_score(0.8, 80),
            selected=False,
            signals={"entity_match": True},
        )
        for index in range(2)
    ]

    out = tmp_path / "out"
    OutputManager().write(PipelineResult(issue=issue, candidates=candidates), out)

    assert len(list((out / "images" / "x1" / "未候选").glob("*.jpg"))) == 1
    assert sum(candidate.local_path is not None for candidate in candidates) == 1
