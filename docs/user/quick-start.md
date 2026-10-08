# 快速开始

本项目对外名称为**周报图片采集工具箱**。程序窗口目前仍显示 **Galgame新闻工具箱**，这是界面名称，不影响使用。

## 环境与安装

- Windows 电脑，Python 3.11 或更高版本。
- 在项目根目录打开 PowerShell，安装桌面版依赖：

```powershell
python -m pip install -e ".[desktop]"
```

可选地，从同一项目目录启动 GUI：

```powershell
python -c "from galgame_news.desktop.app import main; raise SystemExit(main())"
```

也可以双击项目中的 `启动工具箱.pyw`。源码模块 `desktop.app` 没有 `__main__` 启动入口。

动态网页浏览器是可选增强。需要时额外安装浏览器依赖并安装 Chromium：

```powershell
python -m pip install -e ".[desktop,browser]"
python -m playwright install chromium
```

不安装浏览器组件也可运行；GUI 设置中的“动态网页浏览器增强”需要浏览器运行时可用。

## 创建一轮采集

1. 打开“新建任务”，选择周报 `.docx` 文件并填写本期期号。
2. 选择输出目录。默认位置可在“设置”中查看或更改。
3. 选择栏目：新作 `x`、汉化 `h`、周报 `z`，至少选一项。勾选状态只决定本轮抓取范围；文档中原有新闻编号会保留。
4. 默认勾选“跳过视频”。如需收集视频，取消勾选；视频单独审核，已选视频可随最终媒体导出，不会被转换成图片。
5. SocialData 默认关闭。只有配置好自己的密钥并确实需要通过 SocialData 补充 X 媒体时，才在本轮单独启用。
6. 开始任务后可在“抓取进度”查看状态。完成后进入“图片审核”。

## 审核并导出

左侧按新闻筛选，中间切换“已选”“未候选／待复核”“已排除”“视频”。选中一张或多张图片后，按 `A` 标为已选、`R` 排除、`P` 待复核；也可使用界面按钮。按住 `Ctrl` 或 `Shift` 可多选。

右侧可以查看图片、新闻信息和采集依据。“打开来源”会打开来源页面，“打开官网”打开作品官网（如果记录中有），“打开原图”打开记录中的媒体 URL；本地原件保留在任务目录中。视频页可用“播放视频”。

点击“导出图片（含未候选）”后查看提示的导出路径，交付图片位于任务目录的 `final/images`。此目录包含已选图片与符合条件的未候选备选。`review_assets` 是审核内部资源目录，不是用户备选目录。原始文件、索引和审核状态会保留在任务结果中。

暂停会等待在途请求完成当前工作；点“继续”接着处理。需要结束本轮时点“停止任务”，之后可从历史任务恢复续跑。

## 命令行

安装基础包后，可从项目根目录调用：

```powershell
python -m galgame_news run "input/demo.docx" --issue demo --output "output/demo" --no-videos
```

`demo.docx` 为路径示例，请换成自己的文档。`--issue` 必填；始终显式填写 `--output`，避免沿用源码中的开发环境默认路径。支持的可选参数为 `--output`、`--offline`、`--no-videos`、`--config`、`--history-db`、`--llm-provider`、`--llm-model`、`--max-images`。栏目选择与 SocialData 开关只在 GUI 提供。
