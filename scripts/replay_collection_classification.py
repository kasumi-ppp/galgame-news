"""263 frozen collection replay. All transports are closed-world; no API keys."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image, ImageDraw, ImageFont, ImageOps
from galgame_news.config import load_config
from galgame_news.curation.gallery_evidence import reset_selection
from galgame_news.delivery.asset_paths import recover_asset_path
from galgame_news.desktop.i18n import image_type_label, reason_label
from galgame_news.discovery.resolver import DefaultSourceResolver
from galgame_news.domain import CollectionResult, ImageCandidate, Issue, SourceType
from galgame_news.pipeline import PipelineRunner, TaskRequest
from galgame_news.review.session import ReviewSession


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "output/263_reference_audit_20261006T163310"
BASE_RAW = AUDIT / "crawl/263-20261006T083509-db2cd9cc/raw"
X_RAW = ROOT / "output/x_download_fix_20261006_180336/cached_replay/263-20261006T100459-4b35ffd6/raw"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(url):
    parts = urlsplit(url)
    return urlunsplit(parts._replace(fragment=""))


def response(url, body=b"", status=200, mime="text/html; charset=utf-8"):
    return SimpleNamespace(url=url, content=body, status_code=status, headers={"content-type": mime})


def counts(values):
    return dict(candidates=len(values), selected=sum(c.selected for c in values),
                unselected=sum(c.curation_status.value == "unselected" for c in values),
                invalid=sum(c.curation_status.value == "invalid" for c in values),
                types=dict(Counter(c.image_type.value for c in values)),
                downloaded=sum(c.download_status == "downloaded" for c in values))


def contact(values, path):
    if not values:
        return
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    sheet = Image.new("RGB", (1280, ((len(values)+3)//4)*240), "#f4f2f9")
    draw = ImageDraw.Draw(sheet)
    for index, c in enumerate(values):
        x, y = index % 4 * 320, index // 4 * 240
        with Image.open(c.local_path) as image:
            preview = ImageOps.contain(image.convert("RGB"), (308, 190))
        sheet.paste(preview, (x+6, y+6))
        draw.text((x+6,y+202), image_type_label(c.image_type)+" · "+("已选" if c.selected else "待复核"), font=font, fill="#262333")
        draw.text((x+6,y+222), str(c.id)+f"  {c.original_width}×{c.original_height}", font=font, fill="#605970")
    sheet.save(path)


def run(out, resume_task=None):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "summary.json").exists():
        raise ValueError("目录已经包含验收结果，请另选新目录")
    issue = Issue.model_validate(read(AUDIT/"parsed_issue.json"))
    baseline = [ImageCandidate.model_validate(c) for c in read(BASE_RAW/"image_index.json")["candidates"]]
    x_photos = [ImageCandidate.model_validate(c) for c in read(X_RAW/"image_index.json")["candidates"]]
    assert len(x_photos) == 57
    frozen_pages = {}
    protected = {}
    for line in (AUDIT/"page_responses.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        frozen_pages[key(row["requested_url"])] = row
        frozen_pages.setdefault(key(row["url"]), row)
    assets = {}
    unavailable_assets = []
    for raw, candidates in ((BASE_RAW, baseline), (X_RAW, x_photos)):
        protected[str(raw/"image_index.json")] = sha(raw/"image_index.json")
        for c in candidates:
            path = recover_asset_path(c.original_path, raw, c.original_sha256 or c.sha256)
            if path is None:
                unavailable_assets.append(c.id)
                continue
            protected[str(path)] = sha(path)
            c.download_status = "downloaded"
            for url in (c.image_url, c.media_source_url, c.downloaded_url):
                if url:
                    assets[key(url)] = path
    recovered_root = AUDIT / "recovered_gallery_not_baseline"
    for row in read(recovered_root/"index.json"):
        path = Path(row["original_path"]).resolve()
        path.relative_to(recovered_root.resolve())
        assert path.is_file()
        with Image.open(path) as image:
            image.load()
        protected[str(path)] = sha(path)
        assets[key(row["image_url"])] = path
    script_url = "https://key.visualarts.gr.jp/summer_ss/common/js/gallery.js?v=2"
    script_body = (AUDIT/"gallery_js_diagnostic.js").read_bytes()
    requests, missing_pages, missing_images = [], set(), set()

    def page_transport(url, **kwargs):
        requests.append({"kind":"page", "url":url})
        if key(url) == script_url:
            return response(url, script_body, mime="application/javascript; charset=utf-8")
        row = frozen_pages.get(key(url))
        if row:
            return response(row["url"], Path(row["path"]).read_bytes(), row["status"])
        missing_pages.add(url)
        return response(url, b"", 404)

    def image_transport(url, **kwargs):
        requests.append({"kind":"image", "url":url})
        path = assets.get(key(url))
        if path is None:
            missing_images.add(url)
            return response(url, b"", 404)
        with Image.open(path) as image:
            mime = Image.MIME.get(image.format, "application/octet-stream")
        return response(url, path.read_bytes(), mime=mime)

    class Parser:
        def parse(self, *args):
            return None
    class Analyzer:
        def analyze(self, *args):
            return issue
    class CachedX:
        def collect(self, news, source, context):
            photos = []
            for old in x_photos:
                if old.news_id != news.id or key(old.source_url) != key(source.url):
                    continue
                c = old.model_copy(deep=True)
                reset_selection(c)
                c.local_path = c.original_path = None
                c.download_status = "pending"
                c.download_error_code = None
                c.downloaded_url = None
                c.downloadable = False
                c.signals.pop("invalid_reason", None)
                photos.append(c)
            return CollectionResult(candidates=photos)
    class FrozenRunner(PipelineRunner):
        async def _run_async_body(self, *args):
            # Fixture DNS addresses are public. Transports are always injected;
            # these addresses are validated but never contacted.
            self._network_runtime.page_client.resolver = lambda _: ["8.8.8.8"]
            self._network_runtime.image_client.resolver = lambda _: ["8.8.8.8"]
            return await super()._run_async_body(*args)
        def _default_adapter(self, source, *, request=None):
            if source.source_type is SourceType.OFFICIAL_X:
                return CachedX()
            return super()._default_adapter(source, request=request)
    config = load_config()
    config.visual_analysis.enabled = False
    config.network.max_retries = 1
    resolver = DefaultSourceResolver(same_domain_transport=page_transport, search_provider=lambda _: [])
    runner = FrozenRunner(parser=Parser(), analyzer=Analyzer(), resolver=resolver, config=config,
                          source_transport=page_transport, image_transport=image_transport)
    def sink(event):
        if event.kind in {"news_started", "news_completed", "task_failed"}:
            print(event.kind, event.news_index, flush=True)
    result = runner.run(TaskRequest(input_path=ROOT/"input/263.docx", issue_id="263", output_dir=out/"replay",
                                    no_videos=True, use_socialdata_x=False,
                                    resume=resume_task is not None, task_dir=resume_task), sink)
    assert result.status == "completed", result.status
    candidates = result.result.all_candidates
    (out/"fixture_requests.json").write_text(json.dumps(requests,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"network_metrics.json").write_text(json.dumps(runner.network_metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    print("开始核验",len(candidates),"个候选的文件与哈希",flush=True)
    checkpoint = read(result.checkpoint_path)
    errors = []
    for c in candidates:
        if c.download_status != "downloaded":
            continue
        for field, digest in (("local_path", c.output_sha256), ("original_path", c.original_sha256)):
            try:
                path = Path(getattr(c,field))
                assert path.resolve().is_relative_to(result.output_dir.resolve())
                assert sha(path) == digest
                with Image.open(path) as image:
                    image.load()
                    if field == "local_path":
                        assert image.format in {"PNG","JPEG"}
                        assert path.suffix.lower() == (".png" if image.format == "PNG" else ".jpg")
            except Exception as exc:
                errors.append({"id":c.id,"field":field,"error":type(exc).__name__})
    (out/"file_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
    assert not errors, errors
    assert runner.network_metrics["socialdata_requests"] == 0
    assert not list(result.task_dir.rglob("*.part"))
    print("文件核验完成，开始审核导入与导出",flush=True)
    session = ReviewSession.load(result.output_dir)
    assert len(session.images) == len(candidates)
    loaded = {c.id:c for c in session.images}
    published = read(result.output_dir/"image_index.json")["candidates"]
    assert all([e.model_dump() for e in loaded[c["id"]].evidence] == c.get("evidence",[]) for c in published)
    exported = session.export_final()
    x_values = [c for c in candidates if c.source_type is SourceType.OFFICIAL_X]
    assert len(x_values) == 57 and all(c.download_status == "downloaded" for c in x_values)
    # Every accepted original input remains byte-identical.
    assert all(sha(Path(p)) == digest for p,digest in protected.items())
    before = {c.id:c for c in baseline}
    per_news = []
    cards = []
    reason_rows = []
    for news in issue.news_items:
        values = [c for c in candidates if c.news_id == news.id]
        old_values = [c for c in baseline if c.news_id == news.id]
        per_news.append({"sequence":news.sequence,"title":news.title,"before":counts(old_values),"after":counts(values)})
        assert sum(c.selected for c in values) <= 20
        for c in values:
            old = before.get(c.id)
            reason_rows.append({"sequence":news.sequence,"id":c.id,"image_url":c.image_url,
                               "before_type":old.image_type.value if old else None,"before_selected":old.selected if old else None,
                               "after_type":c.image_type.value,"after_selected":c.selected,
                               "reasons":c.selection_reasons,"evidence":[e.model_dump() for e in c.evidence]})
            state = {"selected":"已选","unselected":"待复核","invalid":"无效/下载失败"}[c.curation_status.value]
            preview = '<p>本地夹具没有此原件，媒体链接仍保留。</p>'
            if c.local_path:
                preview = f'<img loading="lazy" src="{html.escape(Path(c.local_path).relative_to(out).as_posix(),quote=True)}">'
            links = [("来源页/原帖",c.source_url),("原图",c.image_url),("新闻入口",c.news_source_url or c.signals.get("root_source_url")),("父页面",c.parent_source_url)]
            link_html = " · ".join(f'<a target="_blank" rel="noreferrer" href="{html.escape(url,quote=True)}">{label}</a>' for label,url in links if url)
            ev = "；".join(f'{e.role}: {e.text} [{e.container}]' for e in c.evidence)
            reasons = "；".join(reason_label(r) for r in c.selection_reasons)
            cards.append(f'<article data-news="{news.sequence}" data-state="{c.curation_status.value}">{preview}<h3>{news.sequence}. {html.escape(news.title)}</h3><p>{state} · {image_type_label(c.image_type)} · {c.original_width or 0}×{c.original_height or 0}</p><p>{html.escape(reasons)}</p><details><summary>图片级证据与来源</summary><p>{html.escape(ev)}</p><p>{html.escape(c.download_error_code or "")}</p></details><p>{link_html}</p></article>')
        if news.sequence in {1,16,27}:
            contact([c for c in values if c.selected and c.local_path], out/f"news_{news.sequence}_selected.jpg")
    summary = {"task_dir":str(result.task_dir),"raw_dir":str(result.output_dir),"total":counts(candidates),
               "x_photos":counts(x_values),"file_errors":errors,"network_metrics":runner.network_metrics,
               "fixture_missing_pages":sorted(missing_pages),"fixture_missing_images":sorted(missing_images),
               "baseline_missing_original_ids":unavailable_assets,"historical_hashes_unchanged":True,
               "review_import_count":len(session.images),"export":exported,
               "comparison_scope":"旧结果含搜索来源；本次仅使用新闻明确链接及预算内官网导航。候选总数变化不能解释为整体发现率。",
               "per_news":per_news,"source_map":checkpoint["source_map"]}
    for name, data in (("summary.json",summary),("classification_changes.json",reason_rows),("fixture_requests.json",requests)):
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>263 采集与分类修复</title>
<style>body{font-family:Microsoft YaHei,sans-serif;background:#f5f3fa;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px}article{background:white;border-radius:12px;padding:12px;overflow-wrap:anywhere}img{width:100%;height:240px;object-fit:contain}p{font-size:14px}a{color:#6954b4}select{padding:8px;margin:10px}article[hidden]{display:none}</style>
<h1>263 期：采集与分类修复</h1><p>冻结页面与既有原图重放；没有访问真实网站、付费 API、LLM 或视频下载。缺少冻结详情页与原件的项不能据此判定真实抓取成功率。</p>
<label>新闻 <select id="news"><option value="all">全部</option>'''
    document += ''.join(f'<option value="{n.sequence}">{n.sequence}. {html.escape(n.title)}</option>' for n in issue.news_items)
    document += '</select></label><label>状态 <select id="state"><option value="all">全部</option><option value="selected">已选</option><option value="unselected">待复核</option><option value="invalid">无效/下载失败</option></select></label><main>'+''.join(cards)+'</main><script>function filter(){document.querySelectorAll("article").forEach(a=>{a.hidden=(news.value!=="all"&&a.dataset.news!==news.value)||(state.value!=="all"&&a.dataset.state!==state.value)})}news.onchange=state.onchange=filter;</script></html>'
    (out/"查看图片.html").write_text(document,encoding="utf-8")
    print(json.dumps({"total":summary["total"],"x":summary["x_photos"],"missing_pages":len(missing_pages),"missing_images":len(missing_images),"raw":str(result.output_dir)},ensure_ascii=False),flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--resume-task",type=Path)
    args = parser.parse_args()
    run(args.output,args.resume_task)
