---
task: v2-domain-storage
model: gpt-5.6-luna
reasoning_effort: max
depends_on: []
allowed_paths:
  - pyproject.toml
  - requirements.txt
  - config/default.toml
  - src/galgame_news/__init__.py
  - src/galgame_news/domain.py
  - src/galgame_news/config.py
  - src/galgame_news/delivery/history.py
  - tests/domain/
  - tests/delivery/test_history.py
---

# 任务 01：领域模型、配置与 SQLite

先读 `docs/architecture/image-prescan-v2.md`。严格实现其中公共模型、枚举、协议和 SQLite v1，不得重命名字段。

## 交付

- 建立 Python 3.11+ `src` 包和 pytest 配置，加入 Pydantic v2、Pillow、httpx、beautifulsoup4、ddgs 等后续明确依赖。
- `domain.py` 实现契约中的模型、枚举、Collection/Pipeline 结果模型及稳定 ID 生成。
- `config.py` 从 TOML 读取评分、过滤、网络和分配配置；默认值来自 `config/default.toml`，非法权重、阈值或数量立即报错。
- `SQLiteHistoryStore` 创建/迁移 v1 表，支持已知图片查询、官方来源查询和按新闻原子记录。
- `MemoryHistoryStore` 仅作为真实可运行的内存实现，供测试和离线流程使用。

## 测试先行

先写并观察失败的测试，再写实现。至少覆盖：未知枚举、稳定 ID、带时区时间、权重校验、空数据库初始化、重复迁移、事务回滚、SHA-256/感知哈希查询和官方域名读取。

## 验证

```powershell
python -m pytest tests/domain tests/delivery/test_history.py -q
```

不得修改架构契约、旧 `image_prescan.py` 或其他任务路径。
