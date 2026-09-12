---
task: v2-curation
model: gpt-5.6-luna
reasoning_effort: max
depends_on: [v2-domain-storage, v2-discovery]
allowed_paths:
  - src/galgame_news/curation/
  - tests/curation/
  - tests/fixtures/images/
---

# 任务 04：图片筛选、去重、评分与分配

先读架构契约和配置模型。不得修改上游接口。

## 交付

- 下载后验证 MIME、魔数、完整性、尺寸和像素数；安全文件名不得包含来源路径片段。
- 过滤 favicon、Logo、按钮及 UI 图；无法确定成人内容时加入 `adult_or_unknown`，不宣称安全。
- 实现 SHA-256、保守感知哈希、同一期与跨期重复标记。
- 按 TOML 权重生成 `ScoreBreakdown`；相关性为主，未知时间强制复核，旧通用图标记 `fallback_old_material`。
- 实现确定性两阶段全期分配：每新闻最多 3 张，全期默认最多 20 张；不足 5 张记录缺口但不补低分图。

## 测试先行

覆盖损坏 JPEG、伪造 MIME、临界尺寸、精确/感知/跨期重复、权重变化、旧图降分、同分排序、新闻多样性、每新闻上限、全期上限和不足 5 张不填充。

## 验证

```powershell
python -m pytest tests/curation -q
```
