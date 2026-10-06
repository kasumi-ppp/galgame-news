# Galgame News X/Twitter 抓取逻辑包

生成日期：2026-09-26

本包是当前项目中与 X/Twitter 图片、视频发现相关代码的只读快照，保留原目录结构，便于审查或移植。它不包含 API Key、Cookie、周报、数据库、缓存和已下载媒体。

## 当前实际工作流程

```text
DOCX 中的 x.com/twitter.com 链接
  -> DefaultSourceResolver 标记为 OFFICIAL_X（可信度 0.9）
  -> PipelineRunner 选择 XAdapter
  -> 所有 X 来源强制标记 X_SOURCE / 人工复核
  -> 无 Bearer Token：读取公开帖子 HTML 元数据
       -> 只接受 og:image / twitter:image
       -> 只接受 pbs.twimg.com/media/ 与 /card_img/
       -> 排除 profile_images、abs.twimg.com、favicon 等
       -> 把 name=small 等改为 name=orig
       -> 解析帖子正文或卡片中明确出现的 YouTube/官网链接
       -> 官网链接只跟进一跳，提取 video/source/iframe/JSON-LD 视频
  -> 有 Bearer Token：仅当外部调用方同时注入 API transport 时解析 API 返回值
       -> image_urls 生成图片候选
       -> entities.urls 的 expanded_url/unwound_url 生成视频候选
  -> SafeHttpClient 校验原 URL 与重定向目标，阻止本机、私网及非公网地址
  -> 图片进入统一下载、低清/Logo/UI 过滤、分类、实体匹配与排序
  -> 视频进入 yt-dlp，受时长、大小、清晰度和每新闻数量上限约束
```

## 图片获取规则

入口在 `src/galgame_news/discovery/adapters/x.py`。

- 无 Token 时不会调用私有接口或绕过登录，只读取帖子公开 HTML 的 Open Graph/Twitter Card 元数据。
- 仅保留 `pbs.twimg.com/media/` 和 `pbs.twimg.com/card_img/`，头像路径 `profile_images` 不会成为候选。
- `common._upgrade_image_url()` 将 X 的 `name=small`、`name=medium` 等参数提升为 `name=orig`，并移除宽高缩放参数。
- 每个候选继承 `OFFICIAL_X` 来源、事件匹配信号以及 `X_SOURCE` 人工复核原因。
- 后续 `curation/validation.py` 继续过滤 SVG、头像、Logo、favicon、banner、thumbnail 和低清图片。

## 视频和外链规则

入口在 `src/galgame_news/video/discovery.py`。

- 支持 X 状态链接、YouTube/youtu.be、MP4、WebM 和 M3U8。
- X 公共 HTML 中普通导航链接不会被当成素材；只有 `tweetText` 或卡片容器内的外链才允许继续处理。
- X 内部的 home、intent、用户页、其他 status 等导航链接被排除。
- API JSON 只读取结构化的 `expanded_url` / `unwound_url`，不从自由文本中猜链接。
- 外部官网只跟进一跳，并在请求前、重定向后执行公网地址校验。
- 最终视频下载由 `video/downloader.py` 使用 yt-dlp 完成，默认最高 1080p、最长 600 秒、最大 1 GiB、每条新闻最多 3 个。

## 安全与凭据

- `SafeHttpClient` 限制协议为 HTTP/HTTPS，阻止 loopback、private、link-local、reserved、multicast 和 unspecified 地址。
- 响应默认上限 5 MiB，支持有限重试，不绕过登录、地区或年龄限制。
- 桌面端凭据存入系统 keyring，序列化和 `repr` 会隐藏值。
- 本包中没有任何真实 Token。

## 已知限制（重要）

1. 生产代码目前没有默认的 X API transport。`Application` 和 `PipelineRunner` 虽会读取 `X_BEARER_TOKEN`，但创建 `XAdapter` 时只传入 `public_transport`；因此设置 Token 后并不会自动调用 X API。API 分支只在测试或外部注入 `transport` 时可用。
2. 桌面设置页可把 X Token 保存到 keyring，但当前抓取链路没有把该值注入 `XAdapter`；实际默认仍依赖进程环境变量。
3. 无 Token 路径依赖 X 是否向匿名请求返回有用的公开 HTML 元数据。登录墙、动态渲染或限流时会返回失败记录并转人工复核。
4. 公开元数据通常只能获得帖子主图/卡片图，不保证拿到轮播中的全部图片。
5. 所有 X 素材都强制人工复核，不会因来源看似官方而自动取消风险标记。
6. `SafeHttpClient` 在没有注入 `public_resolver` 时会调用系统 DNS，即使 HTTP transport 已被测试替身替换。在 DNS 被代理到私网地址的环境中，公开 X 元数据会被安全策略拒绝并记录 `x_public_metadata_error`。这能阻止 SSRF，但也意味着相关单元测试目前并非完全脱离 DNS。

## 关键文件

- `src/galgame_news/discovery/adapters/x.py`：X 图片/API/外链适配器。
- `src/galgame_news/discovery/adapters/common.py`：图片 URL 原图化及候选构造。
- `src/galgame_news/discovery/http.py`：HTTP、重试、大小限制与 SSRF 防护。
- `src/galgame_news/discovery/resolver.py`：X 链接识别和来源类型。
- `src/galgame_news/video/discovery.py`：X/YouTube/官网视频发现与规范化。
- `src/galgame_news/video/downloader.py`：yt-dlp 下载和视频约束。
- `src/galgame_news/curation/validation.py`：头像、Logo、UI、低清素材过滤。
- `src/galgame_news/application.py`、`src/galgame_news/pipeline/runner.py`：适配器接入点。
- `src/galgame_news/settings/credentials.py`：Token 的 keyring 存储。
- `tests/discovery/`：公开元数据、外链、去重和 SSRF 回归测试。

## 验证

在原项目根目录运行：

```powershell
python -m pytest -q tests/discovery/test_x_video_security.py tests/discovery/test_video_adapters.py
```

这些测试使用固定响应，不访问真实 X、不消耗 API Token。`test_discovery.py`
中的两项公开元数据测试没有注入固定 DNS resolver，在受限 DNS 环境中可能因
`private or non-public URL` 失败；这是当前代码的已知测试隔离问题。
