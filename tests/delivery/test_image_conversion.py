from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path

from PIL import Image

from galgame_news.delivery.output import OutputManager
from galgame_news.domain import ImageCandidate, ImageCurationStatus, ImageType, Issue, NewsItem, PipelineResult, SourceType


def _webp_bytes(color=(210, 20, 40, 255)):
    stream = BytesIO()
    Image.new("RGBA", (64, 48), color).save(stream, format="WEBP")
    return stream.getvalue()


def test_output_converts_selected_and_unselected_webp_and_keeps_verifiable_originals(tmp_path):
    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Game CG 更新", body="")
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])
    candidates = []
    source_bytes = _webp_bytes()
    for name, selected, image_type in (
        ("selected", True, ImageType.GAME_CG),
        ("unselected", False, ImageType.UNKNOWN),
    ):
        source = tmp_path / f"{name}.webp"
        source.write_bytes(source_bytes)
        candidates.append(ImageCandidate(
            news_id=item.id,
            image_url=f"https://cdn.example/{name}.webp",
            source_url="https://official.example/news",
            source_type=SourceType.OFFICIAL_SITE,
            image_type=image_type,
            fetched_at=datetime.now(timezone.utc),
            mime_type="image/webp",
            width=64,
            height=48,
            byte_size=len(source_bytes),
            sha256=hashlib.sha256(source_bytes).hexdigest(),
            downloadable=True,
            local_path=str(source),
            selected=selected,
        ))

    output = tmp_path / "out"
    OutputManager().write(PipelineResult(issue=issue, candidates=candidates), output)

    index = json.loads((output / "image_index.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in index["candidates"]}
    for candidate in candidates:
        row = rows[candidate.id]
        preview = output / row["local_path"]
        with Image.open(preview) as image:
            assert image.format == "PNG"
            image.verify()
        original = output / row["original_path"]
        assert original.read_bytes() == source_bytes
        assert row["original_mime_type"] == "image/webp"
        assert row["output_mime_type"] == "image/png"
        assert row["output_sha256"] == hashlib.sha256(preview.read_bytes()).hexdigest()


def test_opaque_photo_converts_to_real_high_quality_jpeg(tmp_path):
    from galgame_news.delivery.image_conversion import ImageConverter

    stream = BytesIO()
    Image.new("RGB", (120, 80), (120, 75, 30)).save(stream, format="WEBP", quality=80)
    candidate = ImageCandidate(
        news_id="n1", image_url="https://cdn.example/photo.webp", source_url="https://site.example/news",
        image_type=ImageType.PHOTO, fetched_at=datetime.now(timezone.utc),
    )
    converted = ImageConverter().convert(stream.getvalue(), candidate)
    assert converted.mime_type == "image/jpeg"
    assert converted.width == 120 and converted.height == 80
    with Image.open(BytesIO(converted.data)) as output:
        assert output.format == "JPEG"


def test_animated_gif_uses_first_frame_and_marks_manual_review(tmp_path):
    frames = [Image.new("RGB", (30, 20), color) for color in ("red", "blue")]
    source = tmp_path / "animated.gif"
    frames[0].save(source, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Game", body="")
    candidate = ImageCandidate(
        news_id=item.id, image_url="https://cdn.example/animated.gif", source_url="https://site.example/news",
        image_type=ImageType.PHOTO, fetched_at=datetime.now(timezone.utc), mime_type="image/gif",
        downloadable=True, local_path=str(source), selected=True,
    )
    out = tmp_path / "out"
    OutputManager().write(PipelineResult(issue=Issue(issue_id="1", input_path="1.docx", news_items=[item]), candidates=[candidate]), out)
    assert candidate.selected is True
    assert candidate.animated_source is True
    assert candidate.animation_frame_index == 0
    assert "animated_source_requires_review" in candidate.selection_reasons
    with Image.open(candidate.local_path) as preview:
        assert preview.format == "PNG"
        assert preview.convert("RGB").getpixel((4, 4))[0] > 180


def test_conversion_failure_keeps_original_and_prevents_selection(tmp_path):
    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Game", body="")
    source = tmp_path / "broken.webp"
    source.write_bytes(b"not an image")
    candidate = ImageCandidate(
        news_id=item.id, image_url="https://cdn.example/broken.webp", source_url="https://site.example/news",
        fetched_at=datetime.now(timezone.utc), mime_type="image/webp", downloadable=True,
        local_path=str(source), selected=True,
    )
    result = PipelineResult(issue=Issue(issue_id="1", input_path="1.docx", news_items=[item]), candidates=[candidate])
    out = tmp_path / "out"
    OutputManager().write(result, out)
    assert candidate.selected is False
    assert candidate.curation_status is ImageCurationStatus.UNSELECTED
    assert candidate.local_path is None
    assert Path(candidate.original_path).read_bytes() == b"not an image"
    assert candidate.signals["conversion_error"]
    assert any(failure.code == "image_conversion_failed" for failure in result.failures)
    OutputManager().write(result, out)
    assert sum(failure.code == "image_conversion_failed" for failure in result.failures) == 1
