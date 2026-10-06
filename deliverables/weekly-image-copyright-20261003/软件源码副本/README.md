# 周报图片采集与管理软件

版本：V0.2.0。Python 包名：weekly_images。

本目录是用于准备软件著作权材料的中性命名源码副本，运行界面和配套说明书使用相同名称。包内保留当前软件实际功能及其限制，主要用途为结构化周报的图片采集、筛选、人工审核与导出。

## 从源码启动

在当前目录打开 PowerShell，执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\python.exe -m weekly_images.desktop
```

命令行离线解析示例：

```powershell
.\.venv\Scripts\python.exe -m weekly_images run "..\04_示例周报.docx" --issue 2026-W40 --output ".\output\demo" --offline --no-videos
```

需要 Python 3.11 及以上，首次安装依赖需要网络。程序中的配置文件位于 config/default.toml。只采集图片时，桌面端勾选“跳过视频，仅抓取图片”。本目录不包含可执行安装包。

示例文档的 example.org 链接仅用于说明格式，不提供实际配图。联网使用时应替换为真实来源并根据需要配置访问凭据。

## 版本与材料维护

不要只修改说明书而保留不一致的界面或程序版本。需要调整申报名称、版本或功能时，应同步更新软件副本、源程序鉴别材料和操作说明书。源码文件清单及页码映射保存在上一级目录。

第三方依赖记录见 packaging/THIRD_PARTY_NOTICES.md。当前版本不提供所有行业、任意格式周报的通用识别保证。
