from datetime import datetime,timezone
from io import BytesIO
import hashlib
from pathlib import Path

from PIL import Image

from galgame_news.delivery.output import OutputManager
from galgame_news.domain import ImageCandidate,ImageCurationStatus,Issue,NewsItem,PipelineResult


def test_invalid_candidate_keeps_landed_original_outside_work(tmp_path):
    item=NewsItem(issue_id="1",sequence=1,section="新作",title="Game",body="CG")
    source=tmp_path/".work"/"image.png"
    source.parent.mkdir()
    image=BytesIO()
    Image.new("RGB",(800,600),"red").save(image,"PNG")
    source.write_bytes(image.getvalue())
    candidate=ImageCandidate(news_id=item.id,image_url="https://site.example/image.png",
        source_url="https://site.example",fetched_at=datetime.now(timezone.utc),
        download_status="downloaded",downloadable=True,original_path=str(source),local_path=str(source),
        original_sha256=hashlib.sha256(image.getvalue()).hexdigest(),
        curation_status=ImageCurationStatus.INVALID,signals={"invalid_reason":"legacy_invalid"})
    result=PipelineResult(issue=Issue(issue_id="1",input_path="fixture.docx",news_items=[item]),filtered_candidates=[candidate])
    OutputManager().write(result,tmp_path/"raw")
    source.unlink()
    assert Path(candidate.original_path).is_file()
    assert candidate.original_path.startswith(str(tmp_path/"raw"))
    assert hashlib.sha256(Path(candidate.original_path).read_bytes()).hexdigest()==candidate.original_sha256
