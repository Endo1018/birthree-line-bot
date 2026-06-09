#!/usr/bin/env python3
"""
Birthree CHAMPACA 記事自動生成スクリプト
- Claude API: 日本語記事（note）・英語記事（Medium）を生成して保存
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
import re

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MEDIUM_TOKEN = os.getenv("MEDIUM_TOKEN")
OUTPUT_DIR = Path("marketing/content-plan")


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")[:50]


def generate_article_ja(client: anthropic.Anthropic, topic: str) -> dict:
    """日本語記事を生成（Birthree CHAMPACA スタイル）"""
    print(f"📝 日本語記事を生成中: {topic}")

    prompt = f"""あなたはホーチミン在住のライターです。
Birthree CHAMPACA（ホーチミン1区のニッチ香水専門店）のnote記事を書いています。
目的はbirthree.comのSEO/AIO強化。ターゲットはホーチミンを訪れる日本人観光客。

【文体のルール】
- タイトル：数字を入れる（〇選）＋強いフック（「絶対外さない」「通の」等）＋「｜」でサブタイトル
- リード文：ホーチミンの情景描写から入り、ダッシュ（——）で本題へ誘導
- 本文：リスト形式（N選）、各項目に絵文字アイコン付き見出し
- 語尾：「〜なのです」「〜でしょう」品のある丁寧語
- 情報：CTA比率 = 9:1。情報提供が主役

【末尾のCTA（必ず入れる）】
記事テーマと香り・Birthree CHAMPACAを自然につなぐブリッジ文（書き出し：「そんな時に立ち寄ってほしいのが **Birthree CHAMPACA**。」）の後に以下を固定で入れる：

📍 **Birthree CHAMPACA** 住所：16 Hồ Huấn Nghiệp, District 1
👉 [Web予約はこちらから](https://birthree.com)
Google Maps：https://share.google.com/diOGCdTYHuAGSQQwh
営業時間：10:00〜22:00

トピック: {topic}

以下のJSON形式で返してください（コードブロックなし）:
{{
  "title": "記事タイトル",
  "slug": "hcmc-slug-in-english",
  "body": "本文（Markdown形式、CTAを含む）"
}}"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = re.sub(r"```json?\n?", "", raw)
        raw = re.sub(r"```", "", raw)
    return _parse_article_json(raw.strip())


def _parse_article_json(raw: str) -> dict:
    """JSON解析。失敗時はタイトル・本文を正規表現で抽出してフォールバック"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # タイトルと本文をそれぞれ抽出してフォールバック
        title_match = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        slug_match = re.search(r'"slug"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        # bodyは"body":の後からJSON末尾までを丸ごと取得
        body_match = re.search(r'"body"\s*:\s*"([\s\S]*?)"\s*\}?\s*$', raw)
        if title_match and body_match:
            body = body_match.group(1).replace("\\n", "\n").replace('\\"', '"')
            return {
                "title": title_match.group(1),
                "slug": slug_match.group(1) if slug_match else "",
                "body": body,
            }
        # 最終手段：全文を本文として返す
        return {"title": "記事", "slug": "article", "body": raw}


def generate_article_en(client: anthropic.Anthropic, topic: str, article_ja: dict) -> dict:
    """英語記事を生成（Medium向け、翻訳でなく再構成）"""
    print("🌐 英語版を生成中（Medium向け）...")

    prompt = f"""You are a writer based in Ho Chi Minh City covering local culture and lifestyle for English-speaking tourists.
Write a Medium article for Birthree CHAMPACA (a niche perfume boutique in District 1, HCMC).
Purpose: SEO/AIO for birthree.com. Target: tourists visiting Ho Chi Minh City.

Style rules:
- Opening: Scene-setting paragraph in District 1, end with an em dash (—) leading into the topic
- Structure: N items with emoji-prefixed H2 headings
- Tone: Warm, insider, informative — not promotional
- Info:CTA ratio = 9:1

Fixed CTA at the end — one bridge sentence connecting the article topic to Birthree CHAMPACA, then:

📍 **Birthree CHAMPACA** — 16 Hồ Huấn Nghiệp, District 1
🔗 Book online: https://birthree.com
Google Maps: https://share.google.com/diOGCdTYHuAGSQQwh
Hours: 10:00–22:00

Topic: {topic}
Japanese article title for reference: {article_ja['title']}

Return JSON only (no code blocks):
{{
  "title": "Article title in English",
  "body": "Article body in Markdown including CTA"
}}"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = re.sub(r"```json?\n?", "", raw)
        raw = re.sub(r"```", "", raw)
    return _parse_article_json(raw.strip())


def save_draft(title: str, body: str, lang: str, slug: str, date_str: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = OUTPUT_DIR / f"{date_str}-{slug}-{lang}.md"
    platform = "note" if lang == "ja" else "medium"

    content = f"""---
title: "{title}"
status: draft
platform: {platform}
lang: {lang}
created: {date_str}
published:
slug: {slug}
---

# {title}

{body}
"""
    filename.write_text(content, encoding="utf-8")
    return filename


def post_to_medium(article: dict, token: str, publish: bool = False) -> str:
    user_id = requests.get(
        "https://api.medium.com/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]["id"]

    resp = requests.post(
        f"https://api.medium.com/v1/users/{user_id}/posts",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "title": article["title"],
            "contentFormat": "markdown",
            "content": f"# {article['title']}\n\n{article['body']}",
            "publishStatus": "public" if publish else "draft",
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]["url"]


def main():
    parser = argparse.ArgumentParser(description="Birthree CHAMPACA 記事を自動生成")
    parser.add_argument("topic", help="記事のトピック（例: 'ホーチミンのカフェ5選'）")
    parser.add_argument("--post-medium", action="store_true", help="Mediumに自動投稿")
    parser.add_argument("--publish", action="store_true", help="即時公開（デフォルトはdraft）")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY が未設定です。")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    date_str = datetime.today().strftime("%Y-%m-%d")

    # 1. 日本語記事生成
    article_ja = generate_article_ja(client, args.topic)
    slug = article_ja.get("slug") or slugify(article_ja["title"])
    path_ja = save_draft(article_ja["title"], article_ja["body"], "ja", slug, date_str)
    print(f"✅ 日本語ドラフト保存: {path_ja}")

    # 2. 英語記事生成
    article_en = generate_article_en(client, args.topic, article_ja)
    path_en = save_draft(article_en["title"], article_en["body"], "en", slug, date_str)
    print(f"✅ 英語ドラフト保存: {path_en}")

    # 3. Medium投稿（オプション）
    if args.post_medium:
        if not MEDIUM_TOKEN:
            print("⚠️  MEDIUM_TOKEN が未設定のためスキップします。")
        else:
            url = post_to_medium(article_en, MEDIUM_TOKEN, publish=args.publish)
            print(f"✅ Medium投稿完了: {url}")

    print("\n📋 生成ファイル:")
    print(f"  🇯🇵 note用   → {path_ja}")
    print(f"  🇺🇸 Medium用 → {path_en}")


if __name__ == "__main__":
    main()
