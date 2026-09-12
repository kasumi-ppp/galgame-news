---
task: v2-integration-hardening
model: gpt-5.6-luna
reasoning_effort: max
depends_on: [v2-delivery-cli]
allowed_paths:
  - tests/integration/
  - tests/fixtures/annotations/
  - scripts/evaluate.py
  - scripts/live_smoke.py
  - pyproject.toml
  - README.md
  - IMAGE_PRESCAN.md
---

# 任务 06：语料回归、评估与集成加固

本任务不新增架构能力；只补真实语料回归、度量、缺陷测试和文档。

## 交付

- 对仓库 `input/` 的 19 份 DOCX 建立只读语料回归：每份均可解析，所有条目有唯一 ID 和最终状态。
- 为第259期和另一份周报建立人工标注 fixture，仅保存期望新闻—图片来源关联，不复制无授权图片。
- `scripts/evaluate.py` 计算召回率、错误匹配率、候选数和待复核数；低于 85% 召回或高于 5% 错误时非零退出。
- `scripts/live_smoke.py` 单独验证官网、Steam、X 和搜索；必须显式传 `--allow-network`，不得进入普通 CI。
- 修复集成中发现的问题时必须先增加失败测试；若修复需要公共接口变化，停止并反馈 Sol Max。

## 验证

```powershell
python -m pytest -q
python scripts/evaluate.py tests/fixtures/annotations
```

人工审核 30 分钟指标记录在评估报告中，不伪造为自动化测试结果。
