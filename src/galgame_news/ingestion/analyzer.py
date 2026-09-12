"""Rule-based entity/event analysis with optional LLM enrichment."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Any

from ..domain import EventType, FailureRecord, FailureStage, ImageNeed, Issue, IssueDraft, NewsItem


NUMBER_WORDS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
NUMBER_UNITS = {"十": 10, "百": 100, "千": 1000}
COUNT_TOKEN_RE = r"[0-9０-９]+|[〇零一二三四五六七八九十百千兩两]+"
COUNTERS = "张張枚点點幅个個"
ANNOUNCEMENT_RE = re.compile(r"更新|公开|公開|公布|新增|追加|发布|發佈|發布|释出|釋出|展示|附上|解禁|登场|登場|掲載|配信|披露|new", re.I)
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]+")
FEATURE_DESCRIPTION_RE = re.compile(r"(?:事件|イベント)?\s*cg\s*(?:鉴赏|鑑賞|模式)", re.I)
IMAGE_NOUNS = (
    (ImageNeed.EXPLICIT_NEW_IMAGE, re.compile(r"キービジュアル|key\s+visual|主(?:视觉|視覺)(?:图|圖)?|视觉图|視覺圖", re.I)),
    (ImageNeed.EXPLICIT_NEW_IMAGE, re.compile(r"(?:事件|イベント)?\s*cg", re.I)),
    (ImageNeed.EXPLICIT_NEW_IMAGE, re.compile(r"背景(?:图片|圖片|画|畫)?")),
    (ImageNeed.EXPLICIT_NEW_IMAGE, re.compile(r"插图|插畫|イラスト|贺图|賀圖")),
)
UNAVAILABLE_RE = re.compile(r"尚未|暂未|暫未|还未|還未|不会|不會|即将|即將|将在|將在|将于|將於|将要|將要|计划|計劃|预计|預計|预定|預定|打算|明天|明日|后天|後天|下(?:周|週|个月|個月|月)")


def _parse_count(value: str) -> int:
    normalized = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if normalized.isdigit():
        return int(normalized)
    total = 0
    current = 0
    for char in normalized:
        if char in NUMBER_WORDS:
            current = NUMBER_WORDS[char]
        elif char in NUMBER_UNITS:
            total += (current or 1) * NUMBER_UNITS[char]
            current = 0
    return total + current


def _noun_count(sentence: str, noun: re.Match[str]) -> int | None:
    before = re.split(r"[，,；;。！？!?\n]", sentence[max(0, noun.start() - 32):noun.start()])[-1]
    after = re.split(r"[，,；;。！？!?\n]", sentence[noun.end():noun.end() + 32], maxsplit=1)[0]
    preceding = re.search(rf"(?P<count>{COUNT_TOKEN_RE})\s*[{COUNTERS}](?:\s*(?:新|新規|新规))?\s*$", before)
    if preceding:
        return _parse_count(preceding.group("count"))
    following = re.match(rf"\s*(?:更新|公开|公開|公布|新增|发布|發布)?(?:了)?\s*(?P<count>{COUNT_TOKEN_RE})\s*[{COUNTERS}]", after, re.I)
    return _parse_count(following.group("count")) if following else None


def _publication_is_current(clause: str, publication: re.Match[str]) -> bool:
    modifiers = re.split(r"但是|不过|不過|但|另外|并且|並且|并|並", clause[:publication.start()])[-1]
    return not re.search(rf"{UNAVAILABLE_RE.pattern}|未\s*$|不(?:会|會|再|曾)?\s*$|将\s*$|將\s*$", modifiers)


def _has_image_evidence(sentence: str, noun: re.Match[str]) -> bool:
    prefix = re.split(r"[；;]", sentence[:noun.start()])[-1]
    clauses = re.split(r"[，,]", prefix)
    local_prefix = clauses[-1]
    unrelated_object = r"发售日|發售日|体验版|體驗版|系统介绍|系統介紹|收录|收錄|内含|內含|包含|包括|其中|情报|情報|内容|內容|介绍|介紹"
    for publication in ANNOUNCEMENT_RE.finditer(local_prefix):
        if not _publication_is_current(local_prefix, publication):
            continue
        target = local_prefix[publication.end():]
        if re.search(unrelated_object, target):
            continue
        if _noun_count(sentence, noun) is not None or re.fullmatch(r"(?:了|新|新的|最新)?\s*", target):
            return True
    suffix = re.split(r"[，,；;]", sentence[noun.end():], maxsplit=1)[0]
    if re.match(rf"\s*(?:{ANNOUNCEMENT_RE.pattern})", suffix, re.I):
        return True
    if len(clauses) < 2:
        return False
    introduced = re.search(rf"(?:{ANNOUNCEMENT_RE.pattern})(?:了)?\s*(?:更新内容|更新內容|新情报|新情報)\s*$", clauses[-2], re.I)
    elaboration = re.fullmatch(rf"\s*(?:包括|其中(?:有|包括|包含))\s*(?:{COUNT_TOKEN_RE})\s*[{COUNTERS}](?:新|新規|新规)?\s*", local_prefix)
    return bool(introduced and _publication_is_current(clauses[-2], introduced) and elaboration)


def detect_image_need(text: str) -> tuple[ImageNeed, int]:
    for sentence in SENTENCE_SPLIT_RE.split(text):
        if re.search(r"happy\s+weekend[^。！？\n]*(?:贺图|賀圖)", sentence, re.I) and not UNAVAILABLE_RE.search(sentence):
            return ImageNeed.EXPLICIT_NEW_IMAGE, 1
    cleaned = FEATURE_DESCRIPTION_RE.sub("", text)
    for sentence in SENTENCE_SPLIT_RE.split(cleaned):
        if not ANNOUNCEMENT_RE.search(sentence):
            continue
        for category, pattern in IMAGE_NOUNS:
            for noun in pattern.finditer(sentence):
                if _has_image_evidence(sentence, noun):
                    if UNAVAILABLE_RE.search(sentence[:noun.start()]):
                        continue
                    return category, _noun_count(sentence, noun) or 1
    return ImageNeed.UNKNOWN, 0


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _extract_games(title: str) -> list[str]:
    values = re.findall(r"《([^》]+)》|『([^』]+)』|「([^」]+)」|【([^】]+)】", title)
    games = [part for match in values for part in match if part]
    if games:
        return _unique(games)
    fallback = re.split(r"(?:发售|発売|公布|公开|公開|更新|完成|制作|製作|体验版|體驗版)", title, maxsplit=1)[0].strip(" -—:：")
    return [fallback] if fallback else []


def _extract_organizations(text: str) -> list[str]:
    return _unique(re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9&.-]*(?:SOFT|GAMES|WORKS|STUDIO|ENTERTAINMENT)", text))


def _extract_people(text: str) -> list[str]:
    people: list[str] = []
    for match in re.finditer(r"(?:原画|原畫|剧本|劇本|脚本|シナリオ|插画|插畫|绘制|繪製)\s*([^。！？\n]+)", text):
        fragment = re.split(r"(?:负责|担当|等|以及|和|、|,|，|绘制|繪製)", match.group(1))[0]
        if fragment.strip():
            people.append(fragment.strip())
    return _unique(people)


def _extract_date(text: str) -> datetime | None:
    match = re.search(r"(?P<y>20\d{2})[年./-](?P<m>\d{1,2})[月./-](?P<d>\d{1,2})日?", text)
    if not match:
        return None
    try:
        return datetime(int(match.group("y")), int(match.group("m")), int(match.group("d")), tzinfo=timezone.utc)
    except ValueError:
        return None


def _event_type(text: str, section: str) -> EventType:
    if re.search(r"汉化|漢化|本地化|翻译|翻譯", text):
        return EventType.LOCALIZATION
    if re.search(r"周边|周邊|商品|グッズ", text):
        return EventType.GOODS
    if re.search(r"体验版|體驗版|demo|试玩|試玩", text, re.I):
        return EventType.DEMO
    if re.search(r"发售|発売|上市|销售|販售", text):
        return EventType.RELEASE
    if re.search(r"活动|活動|イベント|展会|展覽", text):
        return EventType.EVENT
    if ANNOUNCEMENT_RE.search(text):
        return EventType.UPDATE
    if section in {"新作", "新作品"} or re.search(r"新作|新作発表", text, re.I):
        return EventType.NEW_TITLE
    return EventType.UNKNOWN


class RuleBasedNewsAnalyzer:
    def analyze(self, draft: IssueDraft) -> Issue:
        items: list[NewsItem] = []
        for entry in draft.entries:
            text = f"{entry.title}\n{entry.body}"
            image_need, _ = detect_image_need(text)
            event_type = _event_type(text, entry.section)
            games = _extract_games(entry.title)
            organizations = _extract_organizations(text)
            people = _extract_people(entry.body)
            keywords = _unique(games + organizations + people + re.findall(r"(?:CG|主视觉|主視覺|背景图|背景圖|发售日|発売日|更新|公开|公開)", text, re.I))
            importance = {EventType.NEW_TITLE: 0.9, EventType.RELEASE: 0.8, EventType.DEMO: 0.75, EventType.UPDATE: 0.7, EventType.EVENT: 0.65, EventType.GOODS: 0.5, EventType.LOCALIZATION: 0.55, EventType.UNKNOWN: 0.25}[event_type]
            if image_need is not ImageNeed.UNKNOWN:
                importance = min(1.0, importance + 0.05)
            items.append(
                NewsItem(
                    issue_id=draft.issue_id,
                    sequence=entry.sequence,
                    section=entry.section,
                    title=entry.title,
                    author=entry.author,
                    body=entry.body,
                    game_names=games,
                    organizations=organizations,
                    people=people,
                    event_type=event_type,
                    event_at=_extract_date(text),
                    keywords=keywords,
                    source_urls=entry.source_urls,
                    importance=importance,
                    image_need=image_need,
                )
            )
        return Issue(issue_id=draft.issue_id, input_path=draft.input_path, published_at=draft.published_at, news_items=items)


class OpenAINewsAnalyzer:
    """Optional structured enrichment; deterministic rules remain the fallback."""

    def __init__(self, fallback: RuleBasedNewsAnalyzer | None = None, *, provider: str | None = None, model: str | None = None, api_key: str | None = None, invoker: Callable[[dict[str, Any]], Any] | None = None):
        self.fallback = fallback or RuleBasedNewsAnalyzer()
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.invoker = invoker
        self.last_failures: list[FailureRecord] = []

    def analyze(self, draft: IssueDraft) -> Issue:
        self.last_failures = []
        baseline = self.fallback.analyze(draft)
        if not (self.provider and self.model and self.api_key):
            return baseline
        if self.invoker is None:
            self.last_failures.append(FailureRecord(stage=FailureStage.ANALYZE, code="llm_client_unavailable", message="no injected LLM client is configured", retryable=False))
            return baseline
        try:
            payload = self.invoker({"issue_id": draft.issue_id, "entries": [entry.model_dump(mode="json") for entry in draft.entries]})
            enrichments = payload.get("news_items") if isinstance(payload, dict) else payload
            if not isinstance(enrichments, list):
                raise ValueError("structured output must contain a news_items list")
            by_sequence = {int(item["sequence"]): item for item in enrichments if isinstance(item, dict) and "sequence" in item}
            updated: list[NewsItem] = []
            for item in baseline.news_items:
                enrichment = by_sequence.get(item.sequence)
                if enrichment:
                    allowed = {key: value for key, value in enrichment.items() if key in {"game_names", "organizations", "people", "event_type", "event_at", "keywords", "importance", "image_need"}}
                    item = item.model_copy(update=allowed)
                updated.append(item)
            return baseline.model_copy(update={"news_items": updated})
        except Exception as exc:
            self.last_failures.append(FailureRecord(stage=FailureStage.ANALYZE, code="llm_invalid_output", message=str(exc), retryable=False))
            return baseline
