# GitHub 页面维护与发布

本次改版只涉及 README、中文用户文档与离线演示截图。仓库名、程序窗口名和产品代码保持现状。

## About 设置

在仓库首页右侧 About 的齿轮中填写：

**Description**

```text
面向周报编辑的 DOCX 新闻图片采集与人工审核工具，支持官网、X、VNDB 与 Steam 来源。
```

**Topics**

```text
python pyside6 windows image-downloader news docx
```

**Website** 留空。README 无项目许可证徽章；今后确定整体许可证时再更新。没有实际公开 Release 时，不添加“解压即用”的下载承诺或私测包链接。

## 重新生成截图

安装桌面依赖后，在仓库根目录运行：

```powershell
$env:PYTHONPATH = "src"
$env:QT_QPA_PLATFORM = "offscreen"
python scripts/capture_public_docs.py
```

脚本创建隔离的临时任务与内存凭据库，只使用本地绘制的演示图片，不读取用户任务、真实图片或密钥，不运行网络采集。五页截图及总览输出到 `docs/assets/screenshots/`。公开截图中的路径是演示显示值，程序界面名称保持原样。演示图片不是分类评估样本。

重新生成后检查六张图片，尤其是设置页路径、历史记录和技术详情；不上传带有个人路径或密钥的素材。

## 提交与 PR

本轮在 `codex/toolbox-modular-refactor` 提交文档，通过 PR 合入 `main`，不自动合并。发布前核对默认分支是否已经具有首页介绍的功能；如果尚未具备，需要在 PR 中明确关联功能提交。

```powershell
git push origin codex/toolbox-modular-refactor
```

然后打开 [建立 PR 的比较页面](https://github.com/kasumi-ppp/galgame-news/compare/main...codex/toolbox-modular-refactor?expand=1)，确认 base 为 `main`、compare 为当前功能分支；只合入已审阅的变更。

## GitHub 插件没有写入权限时

`403 Resource not accessible by integration` 表示当前 GitHub 插件连接不具备对应写入权限，登录账号和读取仓库成功不等于可写入。此时保留本地提交，可通过已经登录的 PyCharm Git 推送界面或上述命令推送，再创建 PR。

若使用 GitHub 网页手动上传，保留相对目录结构，上传 README、`docs/user/`、`docs/assets/`、本文及截图脚本到功能分支，不能只上传 README 而遗漏引用的图片。二进制截图用 Add file → Upload files；文本可在网页编辑。不要上传 `input/`、`output/`、私测包、凭据文件或 API 密钥。

About 描述与 Topics 属于仓库设置，不随 README 提交更新。当前 GitHub 插件未提供仓库元数据编辑工具时，按本文内容在网页手动设置；这不需要改动项目代码。
