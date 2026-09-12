---
task: v2-discovery
model: gpt-5.6-luna
reasoning_effort: max
depends_on: [v2-domain-storage, v2-ingestion]
allowed_paths:
  - src/galgame_news/discovery/
  - tests/discovery/
  - tests/fixtures/html/
---

# 任务 03：来源发现与 Source Adapter

先读架构契约及已实现领域接口，不得修改公共模型。

## 交付

- `DefaultSourceResolver` 严格执行：DOCX URL → 历史官方域名 → 同域深度 1 → Brave → DDGS。
- 搜索只发现来源；未验证域名保持 `unverified`。结果按规范化 URL 去重并保持确定顺序。
- 实现 `OfficialHtmlAdapter`、`SteamAdapter`、`XAdapter`、`DirectImageAdapter` 和动态/视频占位处理。
- HTML 提取覆盖 Open Graph、Twitter Card、普通/懒加载图片、`srcset`、图片链接和 JSON-LD。
- X 无 token 或失败时保留来源并产生人工复核记录，不尝试绕过限制。
- HTTP 客户端实现公网地址校验、重定向复验、大小上限、3 次重试与 408/429/5xx 策略。
- Adapter 的预期失败全部转为 `FailureRecord`。

## 测试先行

通过本地夹具和注入式 transport 覆盖每个 Adapter；加入 loopback、私网、DNS 解析到私网、重定向到私网、429、超大响应、年龄门和动态页测试。普通测试不得访问真实网络。

## 验证

```powershell
python -m pytest tests/discovery -q
```
