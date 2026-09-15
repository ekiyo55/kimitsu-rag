# -*- coding: utf-8 -*-
"""
月に一度の見返し（第10章）。history.db を読み、次の四つを表示する。
  ① 「資料からは判断できません」を含む回答   … 資料に無いか、検索が外れた質問。FAQ か資料の追加候補
  ② 同じ質問が2回以上聞かれたもの           … よく聞かれる質問。FAQ 候補
  ③ 根拠の資料が一件も付かなかった回答       … 検索が何も拾えなかった質問
  ④ 根拠に使われた資料の回数                 … どの規程がよく引かれているか

使い方:  python review_history.py          直近30日
         python review_history.py 90       直近90日
"""
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"
UNANSWERED = "判断できません"


def normalize(q: str) -> str:
    """同じ質問と見なすための正規化: 空白・句読点・記号を除き、全角英数を半角に、大文字を小文字に"""
    q = q.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
                                  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"))
    return re.sub(r"[\s、。，．,.?？!！「」『』（）()・…]", "", q).lower()


def main(days: int) -> None:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT asked_at, question, answer, sources_json FROM qa_log WHERE asked_at >= ? ORDER BY id", (since,)
    ).fetchall()
    conn.close()
    if not rows:
        print(f"直近{days}日の履歴はありません。")
        return

    print(f"=== 直近{days}日（{since[:10]} 〜 {datetime.now():%Y-%m-%d}）: 質問 {len(rows)}件 ===")

    unanswered = [(a, q) for a, q, ans, _ in rows if UNANSWERED in ans]
    print(f"\n[①「{UNANSWERED}」と答えた質問] {len(unanswered)}件")
    for asked_at, q in unanswered:
        print(f"  {asked_at[:10]}  {q}")

    counts = Counter(normalize(q) for _, q, _, _ in rows)
    first_text = {}
    for _, q, _, _ in rows:
        first_text.setdefault(normalize(q), q)
    repeated = [(n, first_text[k]) for k, n in counts.most_common() if n >= 2]
    print(f"\n[② 2回以上聞かれた質問] {len(repeated)}件")
    for n, q in repeated:
        print(f"  {n}回  {q}")

    no_source = [(a, q) for a, q, _, s in rows if not json.loads(s or "[]")]
    print(f"\n[③ 根拠の資料が付かなかった回答] {len(no_source)}件")
    for asked_at, q in no_source:
        print(f"  {asked_at[:10]}  {q}")

    doc_counter = Counter()
    for _, _, _, s in rows:
        for src in json.loads(s or "[]"):
            doc_counter[src["doc"]] += 1
    print("\n[④ 根拠に使われた資料の回数]")
    for doc, n in doc_counter.most_common():
        print(f"  {n:3d}  {doc}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
