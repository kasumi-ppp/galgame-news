# Galgame 周报图片预搜 v2 架构契约

状态：Frozen v1  
架构负责人：Sol Max（`gpt-5.6-sol` / `max`）  
实现负责人：Luna Max（`gpt-5.6-luna` / `max`）

## 目标与不变量

- 输入是一份 Galgame 周报 DOCX，输出是全期 5–20 张高相关候选图片及人工复核资料。
- 每条独立新闻都必须进入来源解析和候选收集；候选不足时允许全期少于 5 张，禁止用无关旧图补足。
- 所有模块只交换本文件定义的领域模型，不读取其他模块的内部字典结构。
- 单条新闻、单个来源或单张图片失败不得终止整期处理。
- 成人、X、动态页、未知发布时间和匹配不确定的候选不得自动视为可发布。

## 模块和调用方向

生产包固定为 `src/galgame_news/`：

1. `domain.py` 与 `config.py`：公共模型、枚举、配置和协议。
2. `ingestion/`：DOCX 解析、新闻切分、规则分析和可选 LLM 增强。
3. `discovery/`：来源解析、搜索 Provider、网页与平台 Adapter、候选收集。
4. `curation/`：下载安全、有效性过滤、哈希去重、评分及全期图片分配。
5. `delivery/`：SQLite 历史、人工复核数据和输出文件。
6. `application.py` 与 `cli.py`：唯一编排层；业务模块之间不得互相反向导入。

允许依赖方向：`application → ingestion/discovery/curation/delivery → domain`。`domain` 不依赖其他业务模块。

## 公共模型

使用 Pydantic v2。所有时间均为带时区的 ISO 8601；所有集合在序列化时保持确定顺序。

### IssueDraft

- `issue_id: str`
- `input_path: str`
- `published_at: datetime | None`
- `entries: list[NewsDraft]`

### NewsDraft

- `sequence: int`，从 1 开始
- `section: str`
- `title: str`
- `author: str | None`
- `body: str`
- `source_urls: list[str]`

### Issue

- `schema_version: Literal[1] = 1`
- `issue_id: str`
- `input_path: str`
- `published_at: datetime | None`
- `news_items: list[NewsItem]`

### NewsItem

- `id: str`：`sha256(issue_id + "\\0" + sequence + "\\0" + normalized_title)[:16]`
- `issue_id: str`
- `sequence: int`
- `section: str`
- `title: str`
- `author: str | None`
- `body: str`
- `game_names: list[str]`
- `organizations: list[str]`
- `people: list[str]`
- `event_type: EventType`
- `event_at: datetime | None`
- `keywords: list[str]`
- `source_urls: list[str]`
- `importance: float`，范围 0–1
- `image_need: ImageNeed`

### SourceRef

- `url: str`
- `source_type: SourceType`
- `domain: str`
- `discovered_via: DiscoveryMethod`
- `officiality: float`，范围 0–1
- `requires_review: bool`
- `review_reasons: list[ReviewReason]`

### ImageCandidate

- `id: str`：规范化图片 URL 的 SHA-256 前 16 位
- `news_id: str`
- `image_url: str`
- `source_url: str`
- `source_type: SourceType`
- `published_at: datetime | None`
- `fetched_at: datetime`
- `width: int | None`
- `height: int | None`
- `mime_type: str | None`
- `byte_size: int | None`
- `sha256: str | None`
- `perceptual_hash: str | None`
- `downloadable: bool`
- `local_path: str | None`
- `review_reasons: list[ReviewReason]`
- `signals: dict[str, float | str | bool]`
- `score: ScoreBreakdown | None`
- `selected: bool = False`

### ScoreBreakdown

- `relevance: float`
- `freshness: float`
- `source_trust: float`
- `quality: float`
- `duplicate_penalty: float`
- `risk_penalty: float`
- `total: float`，范围 0–100

### FailureRecord

- `stage: FailureStage`
- `news_id: str | None`
- `candidate_id: str | None`
- `code: str`
- `message: str`
- `source_url: str | None`
- `retryable: bool`
- `occurred_at: datetime`

枚举至少包含：

- `EventType`: `new_title`, `release`, `demo`, `update`, `event`, `goods`, `localization`, `unknown`
- `SourceType`: `official_site`, `official_x`, `steam`, `direct_image`, `video`, `unverified`, `unknown`
- `ImageNeed`: `explicit_new_image`, `event_image`, `generic_editorial`, `unknown`
- `ReviewReason`: `x_source`, `age_gate`, `dynamic_page`, `unknown_publish_time`, `uncertain_match`, `close_scores`, `network_restricted`, `fallback_old_material`, `historical_duplicate`, `adult_or_unknown`

未知枚举使用 `unknown`，不得用空字符串。

## 接口

```python
class DocumentParser(Protocol):
    def parse(self, path: Path, issue_id: str) -> IssueDraft: ...

class NewsAnalyzer(Protocol):
    def analyze(self, draft: IssueDraft) -> Issue: ...

class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[SearchResult]: ...

class SourceResolver(Protocol):
    def resolve(self, news_item: NewsItem) -> list[SourceRef]: ...

class SourceAdapter(Protocol):
    def supports(self, source: SourceRef) -> bool: ...
    def collect(self, news_item: NewsItem, source: SourceRef, context: CollectionContext) -> CollectionResult: ...

class ImageCurator(Protocol):
    def curate(self, issue: Issue, candidates: list[ImageCandidate], history: HistoryStore) -> CurationResult: ...

class HistoryStore(Protocol):
    def known_image(self, sha256: str | None, perceptual_hash: str | None) -> HistoricalImage | None: ...
    def sources_for(self, news_item: NewsItem) -> list[SourceRef]: ...
    def record_news_result(self, result: NewsResult) -> None: ...

class OutputManager(Protocol):
    def write(self, result: PipelineResult, output_dir: Path) -> OutputManifest: ...
```

协议变更必须由 Sol Max 更新本文件并提升契约状态；Luna Max 不得自行修改字段、枚举和方法。

## 来源解析顺序

1. DOCX 原始 URL。
2. SQLite 已确认实体与官方域名。
3. 已确认官方域名内深度最多 1 的新闻、Gallery、产品链接。
4. 配置 `BRAVE_SEARCH_API_KEY` 时使用 Brave，否则使用 DDGS。
5. 搜索结果只用于发现来源；未验证官方性的域名保持 `unverified` 并强制复核。

X 配置 `X_BEARER_TOKEN` 时调用官方接口；没有凭据或访问失败时保留帖子 URL，返回 `x_source`/`network_restricted`，不得绕过访问限制。

## 筛选、评分与分配

- URL 仅允许 HTTP/HTTPS；解析和每次重定向都阻止 loopback、link-local、私网、保留地址和非公网目标。
- 默认图片阈值：宽高均至少 300，像素总数至少 120000。
- 校验响应上限、MIME、文件魔数和完整性；Logo、favicon、按钮和 UI 图按 URL、页面语义与尺寸过滤。
- SHA-256 精确去重；感知哈希识别缩放和重编码。跨期命中标记 `historical_duplicate`。
- `config/default.toml` 默认权重：相关性 0.50、时效性 0.20、来源可信度 0.20、质量 0.10。总分为加权正项减去配置化惩罚，再夹紧到 0–100。
- 每条新闻内部最多保留 3 张。全期先为达到最低分的不同新闻各选 1 张，再按总分、重要度和视觉多样性补至 `max_images`，默认 20。
- 低于最低分的图片不选；不足 5 张时记录 `selection_shortfall`，禁止补无关素材。
- 排序并列规则：来源可信度、发布时间、像素数、候选 ID，均为确定性顺序。

## SQLite 与输出

数据库默认 `.state/galgame_news.sqlite3`，schema version 从 1 开始，包含 `runs`、`news_items`、`sources`、`images`、`image_sightings`、`news_images`、`official_domains`。网络请求不持有数据库事务；每条新闻完成后单独事务提交。

输出固定为：

```text
output/{issue}/
  images/{news_id}/{rank}_{candidate_id}.{ext}
  image_index.json
  image_index.md
  failed_items.json
  review_required.json
```

所有 JSON 顶层含 `schema_version: 1`。输出先写同目录临时文件，再使用原子替换。每条新闻的最终状态必须是 `selected`、`not_selected`、`no_candidate` 或 `failed`。

## 错误与安全

- 外部请求最多重试 3 次，指数退避；4xx 除 408/429 外不重试。
- Adapter 将预期失败转换为 `FailureRecord`；只有无效 DOCX、不可写输出目录和不可恢复的数据库损坏属于致命错误。
- API 密钥只能读取环境变量，不得写入源码、配置样例、日志、数据库或输出。
- 旧未跟踪脚本中的明文凭据视为已暴露，必须由所有者轮换或撤销；v2 不导入这些脚本。

## 验收

- 19 份现有 DOCX 均完成处理且每条新闻有状态。
- 第259期和另一份周报人工标注集上的图片召回率不低于 85%，错误匹配率低于 5%。
- 每期人工审核不超过 30 分钟。
- 普通 CI 完全离线；真实网络验证通过单独的 `live-smoke` 命令运行。
