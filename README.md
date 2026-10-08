# Galgame News Toolbox

本项目提供周报 DOCX 的离线解析、来源发现、候选图片/视频整理和人工审核工具。生产代码位于 `src/galgame_news/`，默认策略位于 `config/default.toml`；输入和生成结果保存在本地 `input/`、`output/`，不会纳入 Git。

## 安装与启动

Windows 用户可直接双击根目录的 `00_启动工具箱.lnk`；它指向
`dist/GalgameNewsToolbox/GalgameNewsToolbox.exe`。如果移动了项目目录，请运行
`python packaging/build.py` 重新构建便携版和快捷方式。

```powershell
python -m pip install -r requirements.txt
python -m galgame_news run INPUT.docx --issue ISSUE --output output/ISSUE
```

常用选项包括 `--offline`、`--no-videos`、`--config PATH`、`--history-db PATH` 和 `--max-images N`。需要桌面界面时安装 `.[desktop]`；Windows 便携版构建工具位于 `packaging/`，只读取 `config/default.toml` 和可选的 `assets/`、`bin/`。

程序不会自动发布图片；年龄确认页、动态页面、X 来源以及无法确认“是否本周新增”的素材会进入人工复核。旧的 `image_prescan.py` 入口已退役，运行时会提示上述 `python -m galgame_news run ...` 用法。

工具箱任务完成后会保留完整任务输出（`raw/`、索引、检查点、缓存、审核状态和 `final/` 图片交付），以便后续审核与恢复。已由旧版本精简为仅图片的历史任务不会凭最终图片重建缺失数据。
