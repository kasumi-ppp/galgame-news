# 每周 Galgame 图片预检

`image_prescan.py` 从周报 DOCX 中提取“新作”文章及其官方链接，为编辑人工核对新增静态图片准备候选清单。它不需要 Microsoft Word；网页和 DOCX 处理只使用 Python 标准库，安装 Pillow 后还会启用缩略图/重编码图的感知去重。

## 使用方法

```powershell
python image_prescan.py weekly.docx
python image_prescan.py weekly.docx --output output\weekly-images --section 新作 --max-candidates 4 --timeout 12
python image_prescan.py weekly.docx --offline
python -m pip install Pillow
```

默认输出目录为 `output/image_prescan_<DOCX 文件名>`。`--offline` 不访问网页，适合只检查文档结构及生成待办清单。

重复使用同一个输出目录时，`manifest.json`、`review.csv` 和 `report.md` 会被本次结果覆盖；已有 `images/` 文件不会自动删除，以免误删人工标注或已筛选素材。需要一份完全干净的结果时，请指定新的输出目录，或在确认不再需要旧文件后手工清理旧目录。

## 输出内容

- `manifest.json`：运行参数、文章、来源访问结果、候选图片、哈希、尺寸、置信度及人工复核原因。
- `review.csv`：每张候选图一行；没有候选图或没有新增静态图的文章也有状态行。
- `report.md`：简短中文汇总。
- `images/<文章>/`：下载成功的候选原图；全期按 SHA-256 精确去重，安装 Pillow 时还会对缩放、重编码版本做保守的感知去重。重复来源仍留在清单中并标记 `duplicate`，但不会重复保存文件。

工具会检查 Open Graph/Twitter 图片、普通和懒加载图片、`srcset` 及直接指向图片的链接。候选仅供人工核对；它不会把下载成功视作安全或确定为本次新增图片。

## 限制与安全

X/Twitter、年龄门、动态页面无静态图和抓取失败都会标注为人工复核。感知去重采用保守阈值，编辑仍应检查未识别的裁剪图和误判。工具不会搜索第三方 CG 包、规避登录或年龄限制，也不会去水印、改图或重分发图片。请只在有权编辑和保存的官方来源范围内使用，并在发布前核实版权、内容分级和“是否为本次新增图片”。
