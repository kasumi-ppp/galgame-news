"""Compare the frozen synchronous pipeline with the current network pipeline.

Run ``python scripts/benchmark_async_network.py --mode local|real|all``.  Each
variant imports galgame_news in its own process.  The local fixture keeps
public-looking URLs through URL validation, then maps only fixture.test to a
loopback ThreadingHTTPServer at the injected transport boundary.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


PROJECT = Path(__file__).resolve().parents[1]
BENCH = PROJECT / "output" / "async_benchmark_20261002"
PUBLIC = "https://fixture.test"
DELAY = 0.19


def _images() -> dict[str, tuple[bytes, str]]:
    from PIL import Image, ImageDraw

    result = {}
    for index in range(9):
        image = Image.new("RGB", (720, 480), (25 + index * 19, 58 + index * 12, 125 + index * 9))
        draw = ImageDraw.Draw(image)
        for stripe in range(0, 720, 32):
            draw.rectangle((stripe, (stripe * (index + 2)) % 430, stripe + 16, 480), fill=(190 - index * 8, 80 + index * 9, 45 + stripe % 180))
        draw.text((80, 80), f"Chapter {index // 3 + 1} CG {index + 1}", fill="white")
        suffix, format_name, mime = (("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp"))[index % 3]
        buffer = BytesIO()
        image.save(buffer, format=format_name, quality=90)
        result[f"/media/cg-{index + 1}.{suffix}"] = (buffer.getvalue(), mime)
    with Image.open(BytesIO(result["/media/cg-1.png"][0])) as source:
        duplicate = BytesIO()
        source.convert("RGB").save(duplicate, format="JPEG", quality=96)
    result["/media/cg-1-copy.jpg"] = (duplicate.getvalue(), "image/jpeg")
    return result


def _pages() -> dict[str, str]:
    pages = {
        "/": '<html><title>Fixture official news</title><a href="/product/star-story/">Star Story</a></html>',
        "/product/star-story/": '<html><title>Star Story</title>' + "".join(
            f'<a href="/product/star-story/chapter-{chapter}">Chapter {chapter}</a>' for chapter in range(1, 4)
        ) + "</html>",
    }
    for chapter in range(1, 4):
        pages[f"/product/star-story/chapter-{chapter}"] = (
            f'<html><title>Star Story Chapter {chapter}</title><a href="/product/star-story/chapter-{chapter}/gallery">Gallery</a></html>'
        )
        pages[f"/product/star-story/chapter-{chapter}/gallery"] = (
            f'<html><title>Star Story Chapter {chapter} Gallery</title><section id="gallery">'
            + "".join(
                f'<img src="/media/cg-{index}.{("png", "jpg", "webp")[(index - 1) % 3]}" alt="Star Story CG {index}">'
                for index in range((chapter - 1) * 3 + 1, chapter * 3 + 1)
            ) + ('<img src="/media/cg-1-copy.jpg" alt="Star Story CG 1 alternate encoding">' if chapter == 1 else "") + "</section></html>"
        )
    return pages


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.pages = _pages()
        self.images = _images()
        self.counts: Counter[str] = Counter()
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), FixtureHandler)


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        with self.server.lock:
            self.server.counts[path] += 1
        time.sleep(DELAY)
        if path in self.server.pages:
            payload, mime, status = self.server.pages[path].encode(), "text/html; charset=utf-8", 200
        elif path in self.server.images:
            payload, mime = self.server.images[path]
            status = 200
        else:
            payload, mime, status = b"missing", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@dataclass
class _PublicResponse:
    content: bytes
    headers: dict[str, str]
    status_code: int
    url: str
    encoding: str | None = None

    @property
    def text(self):
        return self.content.decode(self.encoding or "utf-8", errors="replace")


def _worker_import(variant: str):
    root = BENCH / "baseline_src" if variant == "baseline" else PROJECT / "src"
    if variant == "baseline":
        required = (root / "galgame_news" / "pipeline" / "runner.py",
                    BENCH / "baseline_config" / "default.toml")
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"frozen benchmark baseline missing: {', '.join(missing)}")
    sys.path.insert(0, str(root))
    import galgame_news.config as config_module
    if not Path(config_module.__file__).resolve().is_relative_to(root.resolve()):
        raise RuntimeError(f"benchmark imported galgame_news outside selected source tree: {config_module.__file__}")
    if variant == "baseline":
        # The snapshot sits under output, so its ordinary relative default
        # config path would otherwise point at a non-existent sibling.
        config_module._default_path = lambda: BENCH / "baseline_config" / "default.toml"
    return config_module


def _fixture_transport(port: int):
    import httpx

    real_getaddrinfo = socket.getaddrinfo

    def public_dns(host, *args, **kwargs):
        if host == "fixture.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = public_dns

    def get(url, *, timeout=None, headers=None):
        if urlsplit(url).hostname != "fixture.test":
            raise ValueError(f"unexpected fixture transport host: {url}")
        local = f"http://127.0.0.1:{port}{urlsplit(url).path}"
        response = httpx.get(local, timeout=timeout, headers=headers, trust_env=False)
        return _PublicResponse(response.content, dict(response.headers), response.status_code, url, response.encoding)

    return get


def _local_parser():
    from galgame_news.domain import IssueDraft, NewsDraft

    class Parser:
        def parse(self, path, issue_id):
            return IssueDraft(issue_id=issue_id, input_path=str(path), entries=[NewsDraft(
                sequence=1, section="新作", title="《Star Story》Chapter 1 CG公开", body="官方公布了9张新CG。",
                source_urls=[PUBLIC + "/"],
            )])

    return Parser()


def _real_analyzer():
    from galgame_news.ingestion import RuleBasedNewsAnalyzer

    class ThreeOfficialNews:
        def analyze(self, draft):
            issue = RuleBasedNewsAnalyzer().analyze(draft)
            chosen = {1: "nanawind.jp", 2: "liar.co.jp", 5: "entergram.co.jp"}
            issue.news_items = [news for news in issue.news_items if news.sequence in chosen]
            for news in issue.news_items:
                host = chosen[news.sequence]
                news.source_urls = [url for url in news.source_urls if (urlsplit(url).hostname or "").casefold() in {host, "www." + host}]
            if len(issue.news_items) != 3 or any(not news.source_urls for news in issue.news_items):
                raise ValueError("DOCX no longer contains official news 1, 2, and 5")
            return issue

    return ThreeOfficialNews()


def _event_timings(events):
    pending = defaultdict(list)
    durations = defaultdict(float)
    for event in events:
        kind = event["kind"]
        if kind.endswith("_started"):
            pending[kind.removesuffix("_started")].append(event["time"])
        elif kind.endswith(("_completed", "_failed")):
            phase = kind.rsplit("_", 1)[0]
            if pending[phase]:
                durations[phase] += event["time"] - pending[phase].pop(0)
    return {key: round(value, 4) for key, value in durations.items()}


def _summarize(result, elapsed, events, request_counts, network_metrics):
    candidates = list(result.pipeline_result.all_candidates)
    rows = []
    def file_facts(value):
        if not value or not Path(value).is_file():
            return None
        from PIL import Image
        path = Path(value)
        try:
            with Image.open(path) as image:
                detected = Image.MIME.get(image.format)
        except Exception:
            detected = None
        return {"suffix": path.suffix.casefold(), "sha256": sha256(path.read_bytes()).hexdigest(), "detected_mime_type": detected}

    for candidate in candidates:
        rows.append({
            "news_id": candidate.news_id, "image_url": candidate.image_url,
            "source_url": candidate.source_url, "news_source_url": candidate.news_source_url,
            "parent_source_url": candidate.parent_source_url,
            "root_source_url": candidate.signals.get("root_source_url"),
            "source_chain_alternates": candidate.signals.get("source_chain_alternates"),
            "duplicate_of": candidate.signals.get("duplicate_of"),
            "duplicate_kind": candidate.signals.get("duplicate_kind"),
            "duplicate_reason": candidate.signals.get("duplicate_reason"),
            "possible_duplicate_of": candidate.signals.get("possible_duplicate_of"),
            "selection_reasons": sorted(candidate.selection_reasons),
            "selected": candidate.selected, "downloadable": candidate.downloadable,
            "sha256": candidate.original_sha256 or candidate.sha256,
            "original_mime_type": candidate.original_mime_type,
            "output_mime_type": candidate.output_mime_type,
            "local_file": file_facts(candidate.local_path),
            "original_file": file_facts(candidate.original_path),
            "curation_status": str(candidate.curation_status),
        })
    sources = []
    for news in result.issue.news_items:
        news_result = next((entry for entry in result.pipeline_result.news_results if entry.news_item.id == news.id), None) if hasattr(result.pipeline_result, "news_results") else None
        if news_result:
            sources.extend({"url": source.url, "root_url": source.root_url, "parent_url": source.parent_url} for source in news_result.sources)
    checkpoint = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))
    sources = [
        {"news_id": news_id, "url": source["url"], "root_url": source.get("root_url"), "parent_url": source.get("parent_url")}
        for news_id, refs in checkpoint.get("source_map", {}).items() for source in refs
    ]
    failures = [{"stage": str(f.stage), "code": f.code, "source_url": f.source_url, "message": f.message} for f in result.failures]
    stages = _event_timings(events)
    for key, value in network_metrics.get("stage_seconds", {}).items():
        if key == "download_and_processing":
            stages["download"] = round(value, 4)
        elif key in {"resolve", "collect"}:
            stages[key] = round(value, 4)
    downloadable = [row for row in rows if row["downloadable"]]
    file_checks = {
        "downloadable": len(downloadable),
        "missing_original": sum(row["original_file"] is None for row in downloadable),
        "missing_output": sum(row["local_file"] is None for row in downloadable),
        "undecoded_original": sum(bool(row["original_file"]) and not row["original_file"]["detected_mime_type"] for row in downloadable),
        "undecoded_output": sum(bool(row["local_file"]) and not row["local_file"]["detected_mime_type"] for row in downloadable),
        "original_sha_mismatch": sum(bool(row["original_file"]) and row["original_file"]["sha256"] != row["sha256"] for row in downloadable),
        "original_mime_mismatch": sum(bool(row["original_file"]) and row["original_file"]["detected_mime_type"] != row["original_mime_type"] for row in downloadable),
        "output_mime_mismatch": sum(bool(row["local_file"]) and row["local_file"]["detected_mime_type"] != row["output_mime_type"] for row in downloadable),
    }
    return {
        "status": result.status, "elapsed_seconds": round(elapsed, 4),
        "stage_seconds": stages, "request_counts": request_counts,
        "network_metrics": network_metrics,
        "file_checks": file_checks,
        "counts": {"news": len(result.issue.news_items), "sources": len(sources), "candidates": len(rows),
                   "downloadable": sum(row["downloadable"] for row in rows), "selected": sum(row["selected"] for row in rows),
                   "failures": len(failures)},
        "sources": sources, "images": sorted(rows, key=lambda row: (row["news_id"], row["image_url"])),
        "failures": failures, "output_dir": str(result.output_dir), "checkpoint": str(result.checkpoint_path),
        "events": events,
    }


def _run_worker(args):
    _worker_import(args.variant)
    from galgame_news.config import load_config
    from galgame_news.discovery.resolver import DefaultSourceResolver
    from galgame_news.ingestion import DocxDocumentParser, RuleBasedNewsAnalyzer
    from galgame_news.pipeline import PipelineRunner, TaskRequest

    baseline_requests = Counter()
    if args.mode == "real" and args.variant == "baseline":
        import httpx
        original_get = httpx.get
        request_lock = threading.Lock()

        def counted_get(url, *call_args, **call_kwargs):
            with request_lock:
                baseline_requests[urlsplit(str(url)).hostname or "unknown"] += 1
            return original_get(url, *call_args, **call_kwargs)

        httpx.get = counted_get

    task_root = BENCH / args.mode / args.variant
    task_root.mkdir(parents=True, exist_ok=True)
    transport = _fixture_transport(args.port) if args.mode == "local" else None
    resolver = DefaultSourceResolver(
        same_domain_transport=transport, search_provider=lambda _news: [],
        same_domain_depth=3,
    )
    # Passing the snapshot config explicitly keeps the frozen config independent
    # even if this script is run after the working tree's config changes.
    config_path = BENCH / "baseline_config" / "default.toml" if args.variant == "baseline" else PROJECT / "config" / "default.toml"
    config = load_config(config_path)
    if args.mode == "real":
        # The official homepage can list hundreds of unrelated assets. Apply
        # the same bounded per-source cap to both versions for a usable live
        # sample while retaining the three selected news and crawl depth.
        config.search.max_candidates_per_source = 20
    parser = _local_parser() if args.mode == "local" else DocxDocumentParser()
    analyzer = RuleBasedNewsAnalyzer() if args.mode == "local" else _real_analyzer()
    runner = PipelineRunner(parser=parser, analyzer=analyzer, resolver=resolver, config=config,
                            source_transport=transport, image_transport=transport, _legacy_output=True)
    input_path = BENCH / "local_input.txt" if args.mode == "local" else PROJECT / "input" / "226_副本.docx"
    if args.mode == "local":
        input_path.write_text("frozen local fixture v1\n", encoding="utf-8")
    request = TaskRequest(input_path=input_path, issue_id="benchmark-local" if args.mode == "local" else "226",
                          output_dir=task_root / "task", history_db=task_root / "history.sqlite",
                          no_videos=True, use_socialdata_x=False, llm_provider=None, llm_model=None)
    events = []
    began = time.perf_counter()
    def sink(event):
        events.append({"kind": event.kind, "news_id": event.news_id, "message": event.message,
                       "payload": event.payload, "time": round(time.perf_counter() - began, 6)})
    result = runner.run(request, event_sink=sink)
    metrics = getattr(runner, "network_metrics", {})
    request_counts = ({"total": sum(baseline_requests.values()), "by_host": dict(sorted(baseline_requests.items()))}
                      if args.mode == "real" and args.variant == "baseline" else {})
    if args.mode == "real" and args.variant == "async":
        request_counts = {"total": sum(metrics.get(name, {}).get("network_requests", 0) for name in ("pages", "images")),
                          "pages": metrics.get("pages", {}).get("network_requests", 0),
                          "images": metrics.get("images", {}).get("network_requests", 0)}
    summary = _summarize(result, time.perf_counter() - began, events, request_counts, metrics)
    (task_root / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result.status, "result_json": str(task_root / "result.json")}), flush=True)
    return 0 if result.status == "completed" else 1


def _refresh_worker(args):
    """Rebuild metadata from an existing checkpoint without making requests."""
    _worker_import(args.variant)
    from galgame_news.pipeline.contracts import Checkpoint, TaskResult

    task_root = BENCH / args.mode / args.variant
    path = task_root / "result.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    checkpoint_path = Path(old["checkpoint"])
    checkpoint = Checkpoint.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.result is None:
        raise ValueError(f"checkpoint has no result: {checkpoint_path}")
    result = TaskResult(status=old["status"], task_id=checkpoint.task_id,
                        task_dir=checkpoint_path.parent, output_dir=Path(old["output_dir"]),
                        checkpoint_path=checkpoint_path, pipeline_result=checkpoint.result)
    refreshed = _summarize(result, old["elapsed_seconds"], old["events"], old["request_counts"], old.get("network_metrics", {}))
    path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _compare(mode: str, baseline: dict, current: dict) -> dict:
    def logical(rows):
        keys = ("news_id", "image_url", "source_url", "news_source_url", "parent_source_url", "root_source_url",
                "source_chain_alternates", "duplicate_of", "duplicate_kind", "duplicate_reason", "possible_duplicate_of",
                "selection_reasons", "selected", "downloadable", "sha256", "original_mime_type", "output_mime_type",
                "local_file", "original_file", "curation_status")
        return [{key: row.get(key) for key in keys} for row in rows]
    def logical_sources(rows):
        return sorted(rows, key=lambda row: (row["news_id"], row["url"]))
    parity = {
        "sources_equal": logical_sources(baseline["sources"]) == logical_sources(current["sources"]),
        "images_equal": logical(baseline["images"]) == logical(current["images"]),
        "successes_not_lower": current["counts"]["downloadable"] >= baseline["counts"]["downloadable"],
        "selected_not_lower": current["counts"]["selected"] >= baseline["counts"]["selected"],
    }
    improvement = 1 - current["elapsed_seconds"] / baseline["elapsed_seconds"] if baseline["elapsed_seconds"] else 0
    report = {"mode": mode, "baseline": baseline, "async": current, "parity": parity,
              "elapsed_improvement": round(improvement, 4), "goal_30_percent_met": improvement >= .30}
    return report


def _run_variant(mode: str, variant: str, server: FixtureServer | None):
    args = [sys.executable, str(Path(__file__).resolve()), "--worker", "--mode", mode, "--variant", variant]
    if server is not None:
        args.extend(["--port", str(server.server_port)])
    print(f"Running {mode} {variant}...", flush=True)
    process = subprocess.run(args, cwd=PROJECT, text=True, capture_output=True, timeout=900,
                             env={**os.environ, "PYTHONPATH": ""})
    if process.returncode:
        raise RuntimeError(f"{mode} {variant} failed ({process.returncode}):\n{process.stdout[-2000:]}\n{process.stderr[-4000:]}")
    path = BENCH / mode / variant / "result.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    if server is not None:
        result["request_counts"] = {"total": sum(server.counts.values()), "by_path": dict(sorted(server.counts.items()))}
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _run_mode(mode: str, *, report_only: bool = False, refresh: bool = False):
    summaries = {}
    for variant in ("baseline", "async"):
        if refresh:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--refresh", "--mode", mode,
                                      "--variant", variant], cwd=PROJECT, text=True, capture_output=True, timeout=120,
                                     env={**os.environ, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"})
            if process.returncode:
                raise RuntimeError(process.stderr or process.stdout)
        if report_only or refresh:
            summaries[variant] = json.loads((BENCH / mode / variant / "result.json").read_text(encoding="utf-8"))
            continue
        server = FixtureServer() if mode == "local" else None
        if server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
        try:
            summaries[variant] = _run_variant(mode, variant, server)
        finally:
            if server:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
    report = _compare(mode, summaries["baseline"], summaries["async"])
    report_path = BENCH / mode / "comparison.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {mode.capitalize()} network benchmark", "",
             "Frozen baseline and current source were imported in separate processes. Videos, SocialData and LLM were disabled.", ""]
    if mode == "real":
        lines.extend(["The real case parses `input/226_副本.docx`, keeps news 1, 2 and 5, retains only nanawind.jp, liar.co.jp and entergram.co.jp respectively, and disables fallback search in both variants. Each official source is capped at 20 collected candidates; crawl depth remains 3 with the standard 16-page budget. This is a bounded three-news sample, not a full issue crawl.", ""])
    else:
        lines.extend(["The local server delays each response by 190 ms and serves one entry page, a portal, three chapters, three galleries, nine distinct image scenes, and one alternate JPEG encoding of the first PNG scene.", ""])
    lines.extend(["| Measure | Baseline | Async |", "|---|---:|---:|",
                  f"| Elapsed seconds | {report['baseline']['elapsed_seconds']} | {report['async']['elapsed_seconds']} |",
                  f"| HTTP requests | {report['baseline']['request_counts'].get('total', 'see events')} | {report['async']['request_counts'].get('total', 'see events')} |",
                  f"| Downloadable images | {report['baseline']['counts']['downloadable']} | {report['async']['counts']['downloadable']} |",
                  f"| Selected images | {report['baseline']['counts']['selected']} | {report['async']['counts']['selected']} |",
                  f"| Failure records | {report['baseline']['counts']['failures']} | {report['async']['counts']['failures']} |",
                  "", f"Elapsed improvement: {report['elapsed_improvement']:.1%}. 30% goal met: **{report['goal_30_percent_met']}**.",
                  f"Source parity: {report['parity']['sources_equal']}; image metadata parity: {report['parity']['images_equal']}.", "",
                  "## Stage seconds", "", "Baseline stages come from progress events. Async resolve, collect and download are wall times from the runner network metrics; raw events remain in each result JSON.", "",
                  "| Stage | Baseline | Async |", "|---|---:|---:|"])
    for stage in sorted(set(report["baseline"]["stage_seconds"]) | set(report["async"]["stage_seconds"])):
        lines.append(f"| {stage} | {report['baseline']['stage_seconds'].get(stage, 0)} | {report['async']['stage_seconds'].get(stage, 0)} |")
    worker_seconds = report["async"].get("network_metrics", {}).get("stage_seconds", {}).get("image_processing_worker_seconds")
    if worker_seconds is not None:
        lines.extend(["", f"Async image processing worker time summed across threads: {worker_seconds:.4f} seconds. This is not additive wall time."])
    lines.extend(["", "## Failure codes", "", "| Code | Baseline | Async |", "|---|---:|---:|"])
    baseline_failures = Counter(failure["code"] for failure in report["baseline"]["failures"])
    async_failures = Counter(failure["code"] for failure in report["async"]["failures"])
    for code in sorted(set(baseline_failures) | set(async_failures)):
        lines.append(f"| {code} | {baseline_failures[code]} | {async_failures[code]} |")
    lines.extend(["", "## Saved image verification", "", "Every downloadable original and output was decoded; recorded MIME and original SHA-256 were checked against the saved files.", "",
                  "| Check | Baseline | Async |", "|---|---:|---:|"])
    for check in ("downloadable", "missing_original", "missing_output", "undecoded_original", "undecoded_output",
                  "original_sha_mismatch", "original_mime_mismatch", "output_mime_mismatch"):
        lines.append(f"| {check} | {report['baseline']['file_checks'][check]} | {report['async']['file_checks'][check]} |")
    lines.extend(["", "## Artifacts", "", "- [Baseline result](baseline/result.json)", "- [Async result](async/result.json)", "- [Machine comparison](comparison.json)", "- [Baseline image index](baseline/task/image_index.md)", "- [Async image index](async/task/image_index.md)", ""])
    (BENCH / mode / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{mode}: {report['elapsed_improvement']:.1%} faster; parity={report['parity']}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "real", "all"), default="local")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--variant", choices=("baseline", "async"), help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--refresh", action="store_true", help="refresh saved comparisons from checkpoints without HTTP")
    parser.add_argument("--report-only", action="store_true", help="regenerate summaries from existing result JSON")
    args = parser.parse_args()
    BENCH.mkdir(parents=True, exist_ok=True)
    if args.worker:
        if not args.variant or (args.mode == "local" and not args.port and not args.refresh):
            parser.error("worker requires variant and a local port")
        if args.refresh:
            return _refresh_worker(args)
        return _run_worker(args)
    modes = ("local", "real") if args.mode == "all" else (args.mode,)
    for mode in modes:
        _run_mode(mode, report_only=args.report_only, refresh=args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
