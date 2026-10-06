#!/usr/bin/env python3
"""
Galgame News Collector v2
双后端: XAgent MCP + TwitterAPI.io 自动降级
用法:
  python galnews_v2.py                          # 抓取 + 输出 Markdown
  python galnews_v2.py --summarize              # 抓取 + AI 摘要
  python galnews_v2.py --backend mcp            # 强制使用 MCP 后端
  python galnews_v2.py --backend twitterapi     # 强制使用 TwitterAPI.io
"""

import json, os, sys, io, time, hashlib, ssl, asyncio
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ── 编码修复 ──────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 路径配置 ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "sources_v2.json"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
for d in [OUTPUT_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── API Keys ──────────────────────────────────────────
TWITTERAPI_KEY = os.environ.get("TWITTERAPI_KEY", "")
XAGENT_API_KEY = os.environ.get("XAGENT_API_KEY", "")

# ── LLM 配置 ──────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")


# ═══════════════════════════════════════════════════════
#  TwitterAPI.io Backend
# ═══════════════════════════════════════════════════════

class TwitterAPIBackend:
    BASE = "https://api.twitterapi.io"

    def __init__(self, api_key=TWITTERAPI_KEY):
        self.api_key = api_key

    def _request(self, endpoint, params=None, max_retries=3):
        url = f"{self.BASE}{endpoint}"
        if params:
            qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
            url += f"?{qs}"

        ctx = ssl.create_default_context()
        last_error = None
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers={
                    "X-API-Key": self.api_key,
                    "User-Agent": "GalgameNewsBot/2.0"
                })
                resp = urllib.request.urlopen(req, context=ctx, timeout=25)
                return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 429:
                    wait = 3 * (2 ** attempt)
                    print(f"   ⏳ 429 限流，等待 {wait:.0f}s ({attempt+1}/{max_retries})...")
                    time.sleep(wait)
                elif e.code >= 500:
                    if attempt < max_retries - 1:
                        time.sleep(3)
                    else:
                        raise
                else:
                    raise
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    raise
        raise last_error

    def search_tweets(self, query, limit=15):
        data = self._request("/twitter/tweet/advanced_search", {"query": query, "limit": limit})
        return data.get("tweets", [])

    def get_user_timeline(self, username, limit=10):
        data = self._request("/twitter/user/last_tweets", {"username": username, "limit": limit})
        if isinstance(data, list):
            return data
        return data.get("tweets", data.get("data", []))

    @property
    def name(self):
        return "TwitterAPI.io"


# ═══════════════════════════════════════════════════════
#  XAgent MCP Backend
# ═══════════════════════════════════════════════════════

class MCPBackend:
    MCP_URL = "https://mcp.getxagent.com/"

    def __init__(self, api_key=XAGENT_API_KEY):
        self.api_key = api_key
        self._session = None
        self._tools = None
        self._sse_ctx = None
        self._session_ctx = None

    async def _connect(self):
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        headers = {"Accept": "text/event-stream", "X-API-Key": self.api_key}
        self._sse_ctx = sse_client(self.MCP_URL, headers=headers, timeout=30)
        read, write = await self._sse_ctx.__aenter__()

        self._session_ctx = ClientSession(read, write)
        self._session = await self._session_ctx.__aenter__()

        result = await self._session.initialize()
        print(f"   [MCP] 已连接: {result.server_info.name} v{result.server_info.version}")

        tools_result = await self._session.list_tools()
        self._tools = {t.name: t for t in tools_result.tools}
        print(f"   [MCP] 可用工具: {len(self._tools)} 个")

    async def _disconnect(self):
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        if self._sse_ctx:
            try:
                await self._sse_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def _call_tool(self, tool_name, arguments):
        if not self._session:
            await self._connect()
        result = await self._session.call_tool(tool_name, arguments)
        return result

    def _parse_tweets(self, result, source_label=""):
        tweets = []
        for content in (result.content or []):
            if hasattr(content, "text"):
                try:
                    data = json.loads(content.text)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                item["_source"] = source_label
                                tweets.append(item)
                    elif isinstance(data, dict):
                        if "tweets" in data:
                            for item in data["tweets"]:
                                item["_source"] = source_label
                                tweets.append(item)
                        elif "id" in data or "text" in data:
                            data["_source"] = source_label
                            tweets.append(data)
                except json.JSONDecodeError:
                    pass
        return tweets

    async def search_tweets(self, query, limit=15):
        tool_candidates = [
            "search_tweets", "search_twitter", "twitter_search",
            "advanced_search", "search_recent_tweets", "x_search", "search"
        ]
        if not self._tools:
            await self._connect()

        for tool_name in tool_candidates:
            if tool_name in self._tools:
                result = await self._call_tool(tool_name, {"query": query, "count": limit, "max_results": limit})
                return self._parse_tweets(result, f"search:{query[:20]}")

        search_tools = [n for n in self._tools if "search" in n.lower() or "tweet" in n.lower()]
        if search_tools:
            result = await self._call_tool(search_tools[0], {"query": query, "count": limit})
            return self._parse_tweets(result, f"search:{query[:20]}")

        print(f"   [MCP] 未找到搜索工具，可用: {list(self._tools.keys())[:10]}")
        return []

    async def get_user_timeline(self, username, limit=10):
        tool_candidates = [
            "get_user_tweets", "user_timeline", "get_user_timeline",
            "user_tweets", "get_tweets", "timeline"
        ]
        if not self._tools:
            await self._connect()

        for tool_name in tool_candidates:
            if tool_name in self._tools:
                result = await self._call_tool(tool_name, {"username": username, "count": limit})
                return self._parse_tweets(result, f"@{username}")

        print(f"   [MCP] 未找到用户时间线工具")
        return []

    @property
    def name(self):
        return "XAgent MCP"


# ═══════════════════════════════════════════════════════
#  数据收集器
# ═══════════════════════════════════════════════════════

def tweet_key(tweet):
    return tweet.get("id", "") or hashlib.md5(
        (tweet.get("text", "") + tweet.get("createdAt", "")).encode()
    ).hexdigest()

def format_tweet(tweet, source_label=""):
    author = tweet.get("author", {}) or tweet.get("user", {})
    name = author.get("name", author.get("userName", "Unknown"))
    username = author.get("userName", author.get("screen_name", "unknown"))
    text = (tweet.get("text", "") or "").replace("\n", " ")[:300]
    created = tweet.get("createdAt", tweet.get("created_at", ""))
    likes = tweet.get("likeCount", tweet.get("favorite_count", 0))
    rts = tweet.get("retweetCount", tweet.get("retweet_count", 0))
    url = tweet.get("url", "")
    lang = tweet.get("lang", "")

    line = f"**{name}** (@{username})"
    if source_label:
        line += f" [{source_label}]"
    line += f"\n> {text}\n"
    line += f"  {likes}  |   {rts}"
    if lang:
        line += f"  |  {lang}"
    if created:
        line += f"  |  {created}"
    if url:
        line += f"  |  [link]({url})"
    return line

async def collect_mcp(config):
    backend = MCPBackend()
    all_tweets = []
    seen = set()

    try:
        await backend._connect()

        for kw in config.get("keywords", []):
            label = kw["label"]
            query = kw["query"]
            print(f"  [MCP] search: {query} ({label})")
            try:
                tweets = await backend.search_tweets(query, config.get("search_limit", 15))
                for t in tweets:
                    key = tweet_key(t)
                    if key not in seen:
                        seen.add(key)
                        t["_source"] = label
                        all_tweets.append(t)
                print(f"     -> {len(tweets)} tweets")
            except Exception as e:
                print(f"     X  search failed: {e}")
            await asyncio.sleep(0.5)

        for acct in config.get("accounts", []):
            username = acct["username"]
            label = acct["label"]
            print(f"  [MCP] timeline: @{username} ({label})")
            try:
                tweets = await backend.get_user_timeline(username, config.get("timeline_limit", 10))
                for t in tweets:
                    key = tweet_key(t)
                    if key not in seen:
                        seen.add(key)
                        t["_source"] = label
                        all_tweets.append(t)
                print(f"     -> {len(tweets)} tweets")
            except Exception as e:
                print(f"     X  timeline failed: {e}")
            await asyncio.sleep(0.5)

        await backend._disconnect()
    except Exception as e:
        print(f"  X  MCP backend failed: {e}")
        print(f"     Falling back to TwitterAPI.io...")
        return None

    all_tweets.sort(key=lambda t: t.get("createdAt", t.get("created_at", "")), reverse=True)
    return all_tweets

def collect_twitterapi(config):
    backend = TwitterAPIBackend()
    all_tweets = []
    seen = set()
    delay = config.get("request_delay", 1.5)

    for kw in config.get("keywords", []):
        label = kw["label"]
        query = kw["query"]
        print(f"  [TwitterAPI] search: {query} ({label})")
        try:
            tweets = backend.search_tweets(query, config.get("search_limit", 15))
            for t in tweets:
                key = tweet_key(t)
                if key not in seen:
                    seen.add(key)
                    t["_source"] = label
                    all_tweets.append(t)
            print(f"     -> {len(tweets)} tweets")
        except Exception as e:
            print(f"     X  search failed: {e}")
        time.sleep(delay)

    for acct in config.get("accounts", []):
        username = acct["username"]
        label = acct["label"]
        print(f"  [TwitterAPI] timeline: @{username} ({label})")
        try:
            tweets = backend.get_user_timeline(username, config.get("timeline_limit", 10))
            for t in tweets:
                key = tweet_key(t)
                if key not in seen:
                    seen.add(key)
                    t["_source"] = label
                    all_tweets.append(t)
            print(f"     -> {len(tweets)} tweets")
        except Exception as e:
            print(f"     X  timeline failed: {e}")
        time.sleep(delay)

    all_tweets.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
    return all_tweets


# ═══════════════════════════════════════════════════════
#  LLM 摘要
# ═══════════════════════════════════════════════════════

def summarize_with_llm(tweets_text, provider="openai"):
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
        print(f"  X  No {provider.upper()}_API_KEY set, skipping AI summary")
        return None

    prompt = f"""You are a galgame (bishoujo game/visual novel) news editor. Generate a Chinese-language news digest based on the following tweets.

Requirements:
1. List 5-10 noteworthy news items in order of importance
2. Each item: bold title, 1-2 sentence description, source account
3. Preserve specific game names, release dates, demo version info
4. Filter out low-quality ads and spam
5. Add a "This Week's Watchlist" section at the end

Tweets:
{tweets_text}

Output in Markdown, in Chinese."""

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a galgame news editor. Professional, accurate, concise. Output in Chinese."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2500
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
    resp = urllib.request.urlopen(req, context=ctx, timeout=90)
    result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


# ═══════════════════════════════════════════════════════
#  Markdown 输出
# ═══════════════════════════════════════════════════════

def generate_markdown(tweets, config, ai_summary=None, backend_name="TwitterAPI.io"):
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")
    datetime_str = now.strftime("%Y-%m-%d %H:%M CST")

    lines = [
        "#  Galgame News Digest",
        "",
        f"**Generated**: {datetime_str}",
        f"**Source**: X (Twitter) via {backend_name}",
        f"**Tweets**: {len(tweets)}",
        f"**Keywords**: {len(config.get('keywords', []))} groups",
        "",
        "---",
        "",
    ]

    if ai_summary:
        lines.append("##  AI Summary")
        lines.append("")
        lines.append(ai_summary)
        lines.append("")
        lines.append("---")
        lines.append("")

    grouped = defaultdict(list)
    for t in tweets:
        src = t.get("_source", "Other")
        grouped[src].append(t)

    lines.append("##  Raw Tweets")
    lines.append("")

    for src, group in grouped.items():
        lines.append(f"### {src} ({len(group)} tweets)")
        lines.append("")
        for t in group[:20]:
            lines.append(format_tweet(t, src))
            lines.append("")
        lines.append("")

    lines.append("---")
    lines.append(f"*Auto-generated by Galgame News Collector v2 - {date_str}*")

    content = "\n".join(lines)
    filename = f"galnews_{date_str}.md"
    filepath = OUTPUT_DIR / filename
    filepath.write_text(content, encoding="utf-8")

    print(f"\n  Saved: {filepath}")
    return str(filepath)


# ═══════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════

async def main_async(args):
    if not CONFIG_PATH.exists():
        # fallback to old config
        old_config = BASE_DIR / "config" / "sources.json"
        if old_config.exists():
            config_path = old_config
        else:
            print(f"ERROR: No config found at {CONFIG_PATH}")
            return
    else:
        config_path = CONFIG_PATH

    with open(config_path, "r", encoding="utf-8-sig") as f:
        config = json.load(f)

    print("=" * 50)
    print("  Galgame News Collector v2")
    print(f"  Backend: {args.backend}")
    print(f"  Config: {config_path.name}")
    print("=" * 50)

    tweets = None
    backend_name = args.backend

    if args.backend == "mcp":
        tweets = await collect_mcp(config)
        if tweets is None:
            print("  MCP unavailable, falling back to TwitterAPI.io...")
            tweets = collect_twitterapi(config)
            backend_name = "TwitterAPI.io (fallback)"
        else:
            backend_name = "XAgent MCP"
    elif args.backend == "twitterapi":
        tweets = collect_twitterapi(config)
        backend_name = "TwitterAPI.io"
    else:
        print("  Trying XAgent MCP...")
        tweets = await collect_mcp(config)
        if tweets is not None:
            backend_name = "XAgent MCP"
        else:
            print("  Falling back to TwitterAPI.io...")
            tweets = collect_twitterapi(config)
            backend_name = "TwitterAPI.io (fallback)"

    print(f"\n  Total: {len(tweets)} tweets (deduplicated)")

    if not tweets:
        print("  No tweets collected, exiting")
        return

    ai_summary = None
    if args.summarize:
        sample = tweets[:50]
        tweets_text = "\n\n---\n\n".join(
            f"[{t.get('_source', '')}] "
            f"{t.get('author', t.get('user', {})).get('name', '')} "
            f"(@{t.get('author', t.get('user', {})).get('userName', t.get('author', t.get('user', {})).get('screen_name', ''))}): "
            f"{t.get('text', '')}"
            for t in sample
        )
        print(f"\n  Calling {args.provider} for AI summary...")
        try:
            ai_summary = summarize_with_llm(tweets_text, args.provider)
            print("  Summary generated!")
        except Exception as e:
            print(f"  X  Summary failed: {e}")

    generate_markdown(tweets, config, ai_summary, backend_name)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Galgame News Collector v2")
    parser.add_argument("--summarize", action="store_true", help="Enable AI summary")
    parser.add_argument("--provider", default=LLM_PROVIDER, help="LLM provider (openai/deepseek/ollama)")
    parser.add_argument("--backend", default="auto",
                        choices=["auto", "mcp", "twitterapi"],
                        help="Backend: auto, mcp (XAgent), twitterapi (TwitterAPI.io)")
    parser.add_argument("--output", help="Output file path")
    args = parser.parse_args()
    asyncio.run(main_async(args))

if __name__ == "__main__":
    main()
