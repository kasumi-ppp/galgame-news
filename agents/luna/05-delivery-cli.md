---
task: v2-delivery-cli
model: gpt-5.6-luna
reasoning_effort: max
depends_on: [v2-domain-storage, v2-ingestion, v2-discovery, v2-curation]
allowed_paths:
  - src/galgame_news/delivery/output.py
  - src/galgame_news/application.py
  - src/galgame_news/cli.py
  - src/galgame_news/__main__.py
  - image_prescan.py
  - tests/delivery/test_output.py
  - tests/test_application.py
  - tests/test_cli.py
  - README.md
  - IMAGE_PRESCAN.md
---

# 任务 05：输出、编排和 CLI

先读架构契约及任务 01–04 的已实现接口。应用层只负责装配和编排，不复制业务规则。

## 交付

- 实现 `python -m galgame_news run INPUT.docx --issue ISSUE --output output/ISSUE`。
- 支持 `--offline`、`--config`、`--history-db`、`--llm-provider`、`--llm-model`、`--max-images`。
- 每条新闻均执行分析和来源解析；单新闻失败后继续下一条并记录失败。
- 原子生成 `image_index.json`、`image_index.md`、`failed_items.json`、`review_required.json` 和规定图片目录。
- `image_prescan.py` 只做兼容参数转换、弃用提示和新入口调用，不保留业务实现。
- README 给出离线、普通、可选 OpenAI、Brave 和 X 配置示例，但不得出现真实密钥。

## 测试先行

覆盖四种新闻终态、原子输出失败、重复运行、离线流程、单新闻隔离、CLI 参数校验和兼容入口。应用测试使用真实内存模块与本地夹具，只替换外部 transport。

## 验证

```powershell
python -m pytest tests/delivery/test_output.py tests/test_application.py tests/test_cli.py -q
```
