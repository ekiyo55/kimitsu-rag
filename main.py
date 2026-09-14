# -*- coding: utf-8 -*-
"""
RAGアプリ本体。
Ollama+KotobaCoreに実接続し、`documents/`配下の実資料に対して検索・生成を行う。
質問履歴はセッションをまたいで残る永続ログとしてSQLiteに保存する。
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rag_engine import DOCS_DIR, RagIndex, build_prompt, ask_ollama

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/documents", StaticFiles(directory=str(DOCS_DIR)), name="documents")  # KIMITSU_DOCS で切り替えた資料フォルダをそのまま公開
templates = Jinja2Templates(directory="templates")

DB_PATH = Path(__file__).parent / "history.db"

rag_index = RagIndex()
_n_chunks = rag_index.build()
print(f"[起動] {len(rag_index.docs)}件の資料から{_n_chunks}チャンクを索引化しました。")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS qa_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asked_at TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            sources_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def recent_history(limit: int = 6) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT question, answer FROM qa_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [{"question": r["question"], "answer": r["answer"]} for r in rows]


@app.get("/", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(request, "chat.html", {"messages": []})


@app.post("/api/ask", response_class=HTMLResponse)
def ask(request: Request, question: str = Form(...)):
    overview = rag_index.corpus_overview(question)
    if overview is not None:
        answer, sources = overview  # 集計・一覧の質問はLLMを通さず資料メタデータから即答
    else:
        sources = rag_index.search(question, k=5)
        history = recent_history()
        messages = build_prompt(question, sources, history)
        answer = ask_ollama(messages)

    conn = get_db()
    conn.execute(
        "INSERT INTO qa_log (asked_at, question, answer, sources_json) VALUES (?, ?, ?, ?)",
        (
            datetime.now().isoformat(timespec="seconds"),
            question,
            answer,
            json.dumps(
                [{"doc": s["doc"], "snippet": s["snippet"], "file": s["file"], "page": s["page"]} for s in sources],
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    return templates.TemplateResponse(
        request,
        "_message_pair.html",
        {
            "question": question,
            "answer": answer,
            "sources": sources,
        },
    )


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    conn = get_db()
    rows = conn.execute("SELECT * FROM qa_log ORDER BY id DESC").fetchall()
    conn.close()

    entries = [
        {
            "asked_at": r["asked_at"],
            "question": r["question"],
            "answer": r["answer"],
            "sources": json.loads(r["sources_json"]),
        }
        for r in rows
    ]
    return templates.TemplateResponse(request, "history.html", {"entries": entries})
