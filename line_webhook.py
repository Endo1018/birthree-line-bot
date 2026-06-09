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

    return article_ja["title"], article_ja["body"], str(path_ja), str(path_en)


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
                title_ja, body_ja, path_ja, path_en = run_article_generation(topic)
                # 本文を4500字ずつ分割（LINE上限5000字）
                chunks = [body_ja[i:i+4500] for i in range(0, len(body_ja), 4500)]
                messages = [f"✅ できました！\n\n📌 {title_ja}"] + chunks
                # LINEは1回のpushで最大5件なので複数回に分けて送信
                for i in range(0, len(messages), 5):
                    push_messages(user_id, messages[i:i+5])
            except Exception as e:
                push_messages(user_id, [f"❌ 生成エラー: {str(e)}"])

        threading.Thread(target=generate_and_push, daemon=True).start()

    return JSONResponse(content={"status": "ok"})


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
