# Galgame News 图片预搜初版

本仓库当前提交的是周报配图预搜初版：从 DOCX 中识别“新作”新闻、提取官方来源、定位候选图片，并将对应图片整理给编辑人工确认。

## 第259期示例

配图索引和已下载的官网图片见 [`output/259_images_final/`](output/259_images_final/)。其中 X 原帖图片保留原图链接；若本机网络无法访问 `pbs.twimg.com`，可按索引中的链接另存。

## 使用

```powershell
python -m pip install -r requirements.txt
python image_prescan.py "E:\\114514\\259.docx" --output "output\\image_prescan_259"
```

程序不会自动发布图片；X、年龄确认页、动态页面和无法确认“是否本周新增”的素材都会进入人工复核。
