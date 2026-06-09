#!/usr/bin/env python3
"""
LINE Webhook → Birthree CHAMPACA 記事自動生成
トピックを送ると日本語・英語記事を生成してLINEに返信する
"""

import os
import json
import hashlib
import hmac
import base64
import threading
import subprocess
from datetime import datetime

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_SIGNING_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")

app = FastAPI()


# ─────────────────────────────────────────────
# LINE 署名検証
# ─────────────────────────────────────────────

def verify_signature(body: bytes, signature: str) -> bool:
    hash_ = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ─────────────────────────────────────────────
# LINE メッセージ返信
# ─────────────────────────────────────────────

def reply_text(reply_token: str, messages: list[str]):
    """最大5件のテキストをLINEに返信"""
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": m} for m in messages[:5]],
    }
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        },
        json=payload,
        timeout=10,
    )


# ─────────────────────────────────────────────
# 記事生成（article_generator.py のロジックを直接呼び出し）
# ─────────────────────────────────────────────

def run_article_generation(topic: str) -> tuple[str, str]:
    """日本語記事を生成してファイル保存、(タイトル, 本文冒頭) を返す"""
    # article_generator.py と同じ環境にある前提でインポート
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from article_generator import generate_article_ja, generate_article_en, save_draft
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)
    date_str = datetime.today().strftime("%Y-%m-%d")

    # 日本語記事
    article_ja = generate_article_ja(client, topic)
    import re
    slug = re.sub(r"[^\w\s-]", "", article_ja.get("slug", topic).lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:50]

    path_ja = save_draft(article_ja["title"], article_ja["body"], "ja", slug, date_str)

    # 英語記事
    article_en = generate_article_en(client, topic, article_ja)
    path_en = save_draft(article_en["title"], article_en["body"], "en", slug, date_str)

    return article_ja["title"], article_ja["body"], article_en["title"], article_en["body"], str(path_ja), str(path_en)


# ─────────────────────────────────────────────
# Webhook エンドポイント
# ─────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()

    # 署名検証
    signature = request.headers.get("X-Line-Signature", "")
    if LINE_CHANNEL_SECRET and not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = json.loads(body)

    for event in data.get("events", []):
        if event.get("type") != "message":
            continue
        if event["message"].get("type") != "text":
            continue

        reply_token = event["replyToken"]
        topic = event["message"]["text"].strip()

        # 「/記事 トピック」または「トピックだけ」どちらでも動く
        if topic.startswith("/記事 "):
            topic = topic[4:].strip()

        if not topic:
            reply_text(reply_token, ["トピックを送ってください。\n例: ホーチミンのカフェ5選"])
            continue

        user_id = event["source"]["userId"]

        # 受付確認（即座に返信）
        reply_text(reply_token, [f"「{topic}」の記事を生成中です...\n少々お待ちください（約30秒）"])

        # 記事生成をバックグラウンドスレッドで実行（タイムアウト回避）
        def generate_and_push(topic=topic, user_id=user_id):
            try:
                title_ja, body_ja, title_en, body_en, path_ja, path_en = run_article_generation(topic)
                # Notionへ保存
                save_to_notion(topic, title_ja, body_ja, title_en, body_en)
                push_messages(user_id, [
                    f"✅ できました！\n\n📌 {title_ja}\n\nNotionに保存しました。",
                ])
            except Exception as e:
                push_messages(user_id, [f"❌ 生成エラー: {str(e)}"])

        threading.Thread(target=generate_and_push, daemon=True).start()

    return JSONResponse(content={"status": "ok"})


def save_to_notion(topic: str, title_ja: str, body_ja: str, title_en: str, body_en: str):
    """記事をNotionデータベースに保存"""
    from notion_client import Client
    notion = Client(auth=os.getenv("NOTION_TOKEN"))
    db_id = os.getenv("NOTION_DATABASE_ID")

    def text_to_blocks(text: str) -> list:
        """テキストを2000字以内のparagraphブロックに分割"""
        blocks = []
        for line in text.split("\n"):
            # 見出し
            if line.startswith("## "):
                blocks.append({"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":line[3:]}}]}})
            elif line.startswith("# "):
                blocks.append({"object":"block","type":"heading_1","heading_1":{"rich_text":[{"type":"text","text":{"content":line[2:]}}]}})
            else:
                # 2000字制限で分割
                for i in range(0, max(len(line), 1), 2000):
                    chunk = line[i:i+2000]
                    blocks.append({"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":chunk}}]}})
        return blocks[:100]  # Notionは1回100ブロックまで

    notion.pages.create(
        parent={"database_id": db_id},
        properties={
            "Title": {"title": [{"text": {"content": title_ja}}]},
            "Topic": {"rich_text": [{"text": {"content": topic}}]},
            "Date": {"date": {"start": datetime.today().strftime("%Y-%m-%d")}},
            "Status": {"select": {"name": "下書き"}},
        },
        children=text_to_blocks(f"# 🇯🇵 日本語版\n\n{body_ja}\n\n---\n\n# 🇺🇸 English版\n\n{title_en}\n\n{body_en}"),
    )


def github_push(title: str, path_ja: str, path_en: str):
    """生成した記事をGitHubへ自動コミット＆push"""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    def run(cmd):
        subprocess.run(cmd, cwd=repo_dir, capture_output=True)
    run(["git", "config", "user.email", "bot@birthree.com"])
    run(["git", "config", "user.name", "Birthree Bot"])
    run(["git", "add", path_ja, path_en])
    run(["git", "commit", "-m", f"Add article: {title}"])
    run(["git", "push", f"https://x-access-token:{token}@github.com/Endo1018/birthree-line-bot.git", "main"])


def push_messages(user_id: str, messages: list[str]):
    """Push APIでユーザーにメッセージ送信"""
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": m} for m in messages[:5]],
    }
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        },
        json=payload,
        timeout=30,
    )


@app.get("/")
def health():
    return {"status": "ok", "service": "Birthree CHAMPACA LINE Bot"}
