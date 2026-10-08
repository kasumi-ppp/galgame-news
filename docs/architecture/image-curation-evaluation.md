# 图片筛选人工评估

## 生成标注队列

从一次爬取/筛选输出的完整 `image_index.json` 生成 JSON 队列：

```powershell
python scripts/build_annotation_manifest.py `
  output/image-cg-fix-replay-20260928 `
  output/image-cg-fix-replay-20260928/annotation_manifest.json
```

默认包含索引中的全部图片候选。`--limit 300` 会按新闻轮转抽样，保证候选尽量分散到不同新闻；它不保证达到 30 条新闻。标注前应检查图片路径可打开，并将冻结的测试集另存为独立目录，评估期间不要用它调阈值。

队列中的每行是一个“新闻—图片”样本。同一图片关联不同新闻时保留为不同样本。仅填写标签字段，保留候选 ID、预测结果、新闻信息和来源字段：

- `label`: A 优质且与当前新闻相关；B 可能有用、需编辑判断；C 图片有效但基本不适合当前新闻；D 无效数据或损坏图片。
- `entity_match_label`: 当前游戏/IP/商品/活动主体是否匹配，填写 `true` / `false`。
- `source_linked_label`: 图片来源是否能关联到当前新闻周报链接，填写 `true` / `false`。
- `image_type_label`: 人工判定类型，如 `game_cg`、`gameplay_screenshot`、`key_visual`、`cover`、`character_art`、`goods`、`announcement_art`、`photo`、`logo`、`banner`、`ui`。
- `is_game_cg_label`: 是否为当前作品实际游戏 CG，填写 `true` / `false`；官网、Gallery 路径或文件名不能替代人工主体判断。
- `clarity_label`: `best`、`acceptable`、`blurry`、`unusable`；同一重复组中用它标出源像素细节最佳的版本。
- `duplicate_group_label`: 人工确认的同图/近似图组 ID；不同新闻的重复图仍按新闻分别判断。
- `fallback_usable_label`: 在缺少优质图时是否可作 fallback，填写 `true` / `false`。
- `annotator_note`: 记录争议点或需要编辑复核的上下文。

运行评估：

```powershell
python scripts/evaluate.py output/image-cg-fix-replay-20260928
```

不足 300 个已标注候选、30 条新闻，或仍有未标注行时，脚本报告临时指标并以退出码 2 标记 `insufficient_annotations`。达到样本量后，自动验收线为 Selected 的 A Precision ≥95%、主体匹配 Precision ≥99%、Selected 中 C/D 比例 ≤2%、A/B 候选有可打开 PNG/JPG 审阅件的比例 ≥99%。A 类覆盖率只报告、不设硬门槛，待标注协议校准后再定。输出还会报告来源 Precision、真 CG 选中精确率与两区留存率、重复组最佳清晰图命中率、转换编码有效率、原图哈希完整率以及逐新闻入选数和零入选新闻数。旧版 `expected/selected` JSON 仍可读取，但会标为 `legacy_unverified`，不能作为新版人工验收证据。

标注清单同时现场检查审阅件真实编码、PNG/JPG 扩展名与索引 MIME 一致性，并用原始哈希检查下载原件。不存在或不可解码的审阅件不计入 A/B 留存率；未抓取到的图片仍不计为筛选漏选。

不要把未爬到的图算作筛选漏选；本评估只衡量已进入图片候选索引的样本。没有旧原文件的历史候选标记为不可评估，不能伪造恢复结果。

## 本地视觉影子分析

视觉分析配置位于 `[visual_analysis]`，默认关闭。开启后必须提供本地 OpenCLIP 权重路径；运行时只读本地权重和已下载图片，不自动下载权重。安装可选依赖：

```powershell
pip install -e ".[visual]"
```

模块用固定类别提示词、按批推理，并按图片 SHA-256 缓存。结果只写入 `visual_shadow_*` 信号，不参与游戏身份确认、规则分类、排序或自动选择。模型缺失、图片损坏或批次失败不会阻断规则筛选；可通过保持 `enabled = false` 回滚。未完成上述人工标注和冻结集对照前，不应启用视觉结果影响选图。
