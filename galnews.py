#!/usr/bin/env python3
"""
Galgame News Collector — 从 TwitterAPI.io 抓取 galgame 资讯，LLM 摘要输出 Markdown
用法:
  python galnews.py                          # 抓取 + 输出 Markdown（无 AI 摘要）
  python galnews.py --summarize              # 抓取 + OpenAI 摘要
  python galnews.py --summarize --provider deepseek  # 使用 DeepSeek
"""

import json, os, sys, io, time, hashlib
import urllib.request, ssl
from datetime import datetime, timezone, timedelta

# ── 配置 ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "sources.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CACHE_DIR = os.path.join(BASE_DIR, "cache")

TWITTER_API_KEY = os.environ.get("TWITTERAPI_KEY", "")
TWITTER_API_BASE = "https://api.twitterapi.io"

# LLM 配置 — 设置环境变量来启用
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")  # openai / deepseek / ollama
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ── API 调用 ─────────────────────────────────────────
def twitter_request(endpoint, params=None, max_retries=3, base_delay=2.0):
    url = f"{TWITTER_API_BASE}{endpoint}"
    if params:
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url += f"?{qs}"
    ctx = ssl.create_default_context()
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={
                "X-API-Key": TWITTER_API_KEY,
                "User-Agent": "GalgameNewsBot/1.0"
            })
            resp = urllib.request.urlopen(req, context=ctx, timeout=20)
            return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 429:
                wait = base_delay * (2 ** attempt)
                print(f"   ? 429 ????? {wait:.1f}s ?? ({attempt+1}/{max_retries})...")
                time.sleep(wait)
            elif e.code == 400:
                # Bad request, don't retry
                raise
            else:
                if attempt < max_retries - 1:
                    time.sleep(base_delay)
                else:
                    raise
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(base_delay)
            else:
                raise
    raise last_error

def search_tweets(query, limit=15):
    """高级搜索推文"""
    data = twitter_request("/twitter/tweet/advanced_search", {
        "query": query,
        "limit": limit
    })
    return data.get("tweets", [])

def get_user_timeline(username, limit=10):
    """获取用户最新推文"""
    data = twitter_request("/twitter/user/last_tweets", {
        "username": username,
        "limit": limit
    })
    return data if isinstance(data, list) else data.get("tweets", data.get("data", []))

# ── 数据处理 ─────────────────────────────────────────
def tweet_key(tweet):
    """去重用唯一键"""
    return tweet.get("id", "") or hashlib.md5(tweet.get("text", "").encode()).hexdigest()

def format_tweet(tweet, source_label=""):
    """格式化单条推文为可读文本"""
    author = tweet.get("author", {})
    name = author.get("name", "Unknown")
    username = author.get("userName", "unknown")
    text = tweet.get("text", "").replace("\n", " ")
    created = tweet.get("createdAt", "")
    likes = tweet.get("likeCount", 0)
    rts = tweet.get("retweetCount", 0)
    url = tweet.get("url", "")
    lang = tweet.get("lang", "")

    # 截断过长文本
    if len(text) > 280:
        text = text[:277] + "..."

    line = f"**{name}** (@{username})"
    if source_label:
        line += f" [{source_label}]"
    line += f"\n> {text}\n"
    line += f"❤️ {likes}  🔁 {rts}  🌐 {lang}  |  {created}"
    if url:
        line += f"  |  [链接]({url})"
    return line

# ── LLM 摘要 ─────────────────────────────────────────
def summarize_with_llm(tweets_text, provider="openai"):
    """调用 LLM 生成中文资讯摘要"""
    if provider == "openai":
        api_key = OPENAI_API_KEY
        base_url = OPENAI_BASE_URL
        model = LLM_MODEL
    elif provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", OPENAI_API_KEY)
        base_url = "https://api.deepseek.com/v1"
        model = "deepseek-chat"
    elif provider == "ollama":
        api_key = "ollama"
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    else:
        return None

    if not api_key:
        print(f"⚠️  未设置 {provider.upper()}_API_KEY，跳过 AI 摘要")
        return None

    prompt = f"""你是一个 galgame（美少女游戏/视觉小说）资讯编辑。请根据以下推文，生成一份中文资讯简报。

要求：
1. 按重要性排序，列出 5-10 条值得关注的资讯
2. 每条包含：标题（加粗）、简要说明（1-2句）、来源账号
3. 如果推文提到具体游戏名称、发售日期、体验版信息，务必保留
4. 过滤掉低质量的广告和垃圾信息
5. 最后附一个「本周关注」小节，列出值得留意的新作/动态

推文列表：
{tweets_text}

请用 Markdown 格式输出。"""

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是 galgame 资讯编辑，专业、准确、简洁。用中文输出。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2000
    }

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )
    ctx = ssl.create_default_context()
    resp = urllib.request.urlopen(req, context=ctx, timeout=60)
    result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]

# ── 主流程 ───────────────────────────────────────────
def collect_all(config):
    """收集所有推文"""
    all_tweets = []
    seen = set()

    # 关键词搜索
    for kw in config.get("keywords", []):
        label = kw["label"]
        query = kw["query"]
        print(f"🔍 搜索: {query} ({label})")
        try:
            tweets = search_tweets(query, config.get("search_limit", 15))
            for t in tweets:
                key = tweet_key(t)
                if key not in seen:
                    seen.add(key)
                    t["_source"] = label
                    all_tweets.append(t)
            print(f"   → {len(tweets)} 条")
        except Exception as e:
            print(f"   ⚠️  搜索失败: {e}")
        time.sleep(0.3)  # 礼貌的速率限制

    # 账号时间线
    for acct in config.get("accounts", []):
        username = acct["username"]
        label = acct["label"]
        print(f"📋 获取: @{username} ({label})")
        try:
            tweets = get_user_timeline(username, config.get("timeline_limit", 10))
            for t in tweets:
                key = tweet_key(t)
                if key not in seen:
                    seen.add(key)
                    t["_source"] = label
                    all_tweets.append(t)
            print(f"   → {len(tweets)} 条")
        except Exception as e:
            print(f"   ⚠️  获取失败: {e}")
        time.sleep(0.3)

    # 按时间排序（最新在前）
    all_tweets.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
    return all_tweets

def generate_markdown(tweets, config, ai_summary=None):
    """生成 Markdown 资讯文件"""
    now = datetime.now(timezone(timedelta(hours=8)))  # Asia/Shanghai
    date_str = now.strftime("%Y-%m-%d")
    datetime_str = now.strftime("%Y-%m-%d %H:%M CST")

    lines = [
        f"# 🎮 Galgame 资讯简报",
        f"",
        f"**生成时间**: {datetime_str}",
        f"**数据来源**: X (Twitter) via TwitterAPI.io",
        f"**收录推文**: {len(tweets)} 条",
        f"",
        f"---",
        f"",
    ]

    # AI 摘要
    if ai_summary:
        lines.append("## 🤖 AI 摘要")
        lines.append("")
        lines.append(ai_summary)
        lines.append("")
        lines.append("---")
        lines.append("")

    # 按来源分组
    lines.append("## 📡 原始推文")
    lines.append("")

    # 按 source 分组
    from collections import defaultdict
    grouped = defaultdict(list)
    for t in tweets:
        src = t.get("_source", "其他")
        grouped[src].append(t)

    for src, group in grouped.items():
        lines.append(f"### {src}（{len(group)} 条）")
        lines.append("")
        for t in group[:20]:  # 每组最多 20 条
            lines.append(format_tweet(t, src))
            lines.append("")
        lines.append("")

    lines.append("---")
    lines.append(f"*由 Galgame News Collector 自动生成 · {date_str}*")

    content = "\n".join(lines)

    # 保存
    filename = f"galnews_{date_str}.md"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n✅ 资讯已保存: {filepath}")
    return filepath

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Galgame News Collector")
    parser.add_argument("--summarize", action="store_true", help="启用 AI 摘要")
    parser.add_argument("--provider", default=LLM_PROVIDER, help="LLM 提供商 (openai/deepseek/ollama)")
    parser.add_argument("--output", help="输出文件路径（覆盖默认）")
    args = parser.parse_args()

    # 加载配置
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    print("=" * 50)
    print("🎮 Galgame News Collector")
    print("=" * 50)

    # 收集
    tweets = collect_all(config)
    print(f"\n📊 共收集 {len(tweets)} 条推文（去重后）")

    if not tweets:
        print("⚠️  没有收集到推文，退出")
        return

    # AI 摘要
    ai_summary = None
    if args.summarize:
        # 准备推文文本（取前 50 条给 LLM）
        sample = tweets[:50]
        tweets_text = "\n\n---\n\n".join(
            f"[{t.get('_source', '')}] {t.get('author', {}).get('name', '')} (@{t.get('author', {}).get('userName', '')}): {t.get('text', '')}"
            for t in sample
        )
        print(f"\n🤖 正在调用 {args.provider} 生成摘要...")
        try:
            ai_summary = summarize_with_llm(tweets_text, args.provider)
            print("✅ 摘要生成完成")
        except Exception as e:
            print(f"⚠️  摘要生成失败: {e}")

    # 生成 Markdown
    generate_markdown(tweets, config, ai_summary)

if __name__ == "__main__":
    main()
