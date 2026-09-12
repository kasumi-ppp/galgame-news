---
task: v2-ingestion
model: gpt-5.6-luna
reasoning_effort: max
depends_on: [v2-domain-storage]
allowed_paths:
  - src/galgame_news/ingestion/
  - tests/ingestion/
  - tests/fixtures/docx/
---

# 任务 02：DOCX 解析与新闻分析

先读架构契约和任务 01 已实现的领域模型。不得修改 `domain.py` 或 `config.py`。

## 交付

- `DocxDocumentParser` 直接读取 OOXML，输出 `IssueDraft`；兼容 `标题#作者#`、缺失结尾 `#`、拼接 URL 和栏目变化。
- 排除目录、页眉页脚、纯署名和编辑说明；所有真实新闻栏目均保留，不再只筛选“新作”。
- `RuleBasedNewsAnalyzer` 提取游戏名、公司、人物、日期、关键词、事件类型、重要度和图片需求信号。
- `OpenAINewsAnalyzer` 是可选增强器：只有显式配置 provider、model、凭据才调用；输出必须重新经过 Pydantic 校验，失败时返回完整规则结果并记录非致命失败。
- 将原脚本成熟的图片意图判断迁移为 ingestion 内部实现，不复制两份规则。

## 测试先行

覆盖标题边界、URL fragment、栏目切换、中文数字图片数量、未来/未发布图片、LLM 缺失配置、非法结构化输出和 LLM 网络失败回退。测试使用生成的最小 DOCX，不访问网络。

## 验证

```powershell
python -m pytest tests/ingestion -q
```
