# -*- coding: utf-8 -*-
"""
履歴(history.db)に残っている質問をもう一度投げ、当時の答えと今の答えを並べて表示する。
モデルやライブラリを更新したあと、答えが変わっていないかを確かめるための道具（第10章）。

使い方:  python replay_history.py            直近5件
         python replay_history.py 10         直近10件
"""
import json
import sqlite3
import sys
from pathlib import Path

from rag_engine import RagIndex, build_prompt, ask_ollama

DB_PATH = Path(__file__).parent / "history.db"


def main(limit: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT asked_at, question, answer, sources_json FROM qa_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    if not rows:
        print("履歴がありません。")
        return
    idx = RagIndex()
    idx.build()
    for asked_at, question, old_answer, old_sources_json in rows:
        overview = idx.corpus_overview(question)
        if overview is not None:
            new_answer, sources = overview
        else:
            sources = idx.search(question, k=5)
            new_answer = ask_ollama(build_prompt(question, sources, []))
        # 文面は同じ質問でも毎回少し変わるので、根拠（資料名の集合）が同じかを先に見る
        old_docs = sorted({s["doc"] for s in json.loads(old_sources_json or "[]")})
        new_docs = sorted({s["doc"] for s in sources})
        print("=" * 60)
        print(f"Q ({asked_at}): {question}")
        print(f"根拠: {'一致' if old_docs == new_docs else '※ 変化あり'}  当時={old_docs}  今={new_docs}")
        print(f"--- 当時の答え ---\n{old_answer}")
        print(f"--- 今の答え ---\n{new_answer}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
