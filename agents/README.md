# Luna Max 实施任务索引

这些文件是仓库内任务简报，不会被 Codex 自动加载。创建新任务时，请把对应文件作为唯一需求入口，并让任务先阅读架构契约。

## 固定上下文

- 工作树：`E:\project\galgame news\.superpowers\worktrees\image-prescan-v2`
- 分支：`codex/image-prescan-v2`
- 架构契约：`docs/architecture/image-prescan-v2.md`
- 模型：`gpt-5.6-luna`
- 推理强度：`max`
- 开发方式：测试先行；不得自行修改跨模块接口和领域模型。

## 执行顺序

| 顺序 | 新任务标题 | 必读简报 | 前置任务 |
|---:|---|---|---|
| 1 | v2 领域模型与 SQLite | `agents/luna/01-domain-storage.md` | 无 |
| 2 | v2 DOCX 与新闻分析 | `agents/luna/02-ingestion.md` | 1 |
| 3 | v2 来源发现与 Adapter | `agents/luna/03-discovery.md` | 1、2 |
| 4 | v2 图片筛选评分分配 | `agents/luna/04-curation.md` | 1、3 |
| 5 | v2 输出与 CLI 集成 | `agents/luna/05-delivery-cli.md` | 1–4 |
| 6 | v2 语料回归与加固 | `agents/luna/06-integration-hardening.md` | 1–5 |

不要并行运行会修改同一工作树的任务。每个任务完成后应提交到当前分支，并把提交哈希和测试结果回复给 Sol Max 审查。

## 可复制的新任务提示

```text
请在工作树 E:\project\galgame news\.superpowers\worktrees\image-prescan-v2 中工作，使用 gpt-5.6-luna、max 推理强度。先完整阅读 docs/architecture/image-prescan-v2.md，再完整阅读指定的 agents/luna/NN-*.md；后者是本任务唯一实施简报。严格测试先行，只修改 allowed_paths。若需要变更公共模型、枚举、协议或其他模块接口，立即停止并向 Sol Max 报告，不要自行修改。完成后运行简报中的验证命令、提交当前分支，并回复提交哈希、测试摘要和遗留风险。
```
