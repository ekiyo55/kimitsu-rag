# -*- coding: utf-8 -*-
"""
実RAGパイプライン本体。
資料読み込み(docx/pdf) → KotobaCoreでチャンク分割・索引化 → 検索 → Ollamaで回答生成。

設計方針(企画書_v1.md参照):
- 検索・前処理の重い処理はKotobaCore、LLMは「出力編集」の軽い役割に限定する
- GPU不要。ただし実測の結果、KotobaCoreの構造的特徴量(キーワード・エンティティ等)だけの
  検索(InMemoryRetrieverのembed=None)は、規程の条文と一致する用語で質問した場合は機能するが、
  言い換えられた自然文の質問には弱いことが判明した(2026-09-14実測、検証チェックリストG節に記録)。
  そのためOllamaの軽量埋め込みモデル(bge-m3、CPUのみで動作)による意味検索をKotobaCoreの
  再ランキングと組み合わせるハイブリッド構成にした
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import docx
import pypdfium2 as pdfium
from kotobacore import Analyzer
from kotobacore.rag import InMemoryRetriever

# 資料フォルダは環境変数 KIMITSU_DOCS で切り替えられる（例: 社内規程用と研究資料用を目的別に分ける。第8章）
DOCS_DIR = Path(os.environ.get("KIMITSU_DOCS") or (Path(__file__).parent / "documents"))
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embed"
DEFAULT_MODEL = "qwen3:1.7b"
EMBED_MODEL = "bge-m3"

MAX_HISTORY_CHARS = 4000  # チャットに含める過去履歴の予算(日本語1文字≒1トークンの安全側簡易カウント)


def _docx_paragraph_text(paragraph) -> str:
    """python-docxのparagraph.textはスマートタグ内テキストを読み飛ばすため、
    XMLの<w:t>ノードを直接辿って抽出する(2026-09-14に発見・検証済みのバグ対策)。"""
    ts = paragraph._p.xpath(".//w:t")
    return "".join(t.text or "" for t in ts)


def extract_docx_text(path: Path) -> str:
    d = docx.Document(str(path))
    parts = [_docx_paragraph_text(p) for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    parts.append(_docx_paragraph_text(p))
    return "\n".join(t for t in parts if t.strip())


_ARTICLE_LABEL = re.compile(
    r"(?<=[。）)])[ 　]*(第[0-9０-９一二三四五六七八九十百]+条(?:の[0-9０-９一二三四五六七八九十]+)?[ 　]*[（(][^（）()]{1,40}[）)])[ 　]*"
)
_ARTICLE_LABEL_AT_LINE_START = re.compile(
    r"^(第[0-9０-９一二三四五六七八九十百]+条(?:の[0-9０-９一二三四五六七八九十]+)?[ 　]*[（(][^（）()]{1,40}[）)])[ 　]+(?=\S)", re.M
)


def normalize_article_lines(text: str) -> str:
    """PDFから取り出した文章で、条番号と本文が同じ行に並んでいるときに条番号を独立した行にする。
    「第3条（精算のタイミング） 経費精算は…」→ 見出し行「第3条（精算のタイミング）」＋本文行。
    条番号だけの行はKotobaCoreが見出しと認識する（第3章の実験）。文中の参照（「就業規則第○条に規定する」）は
    直前が句点・閉じ括弧のときだけ対象にすることで巻き込まない。"""
    nl = "\n"
    text = _ARTICLE_LABEL.sub(lambda m: nl + m.group(1) + nl, text)
    text = _ARTICLE_LABEL_AT_LINE_START.sub(lambda m: m.group(1) + nl, text)
    return text


def extract_xlsx_text(path: Path) -> str:
    """1行を「シート名 | 見出し: 値 | 見出し: 値…」の1行にする。結合セルで空になる続きの行は直前の値で補う（前方補完）。"""
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            continue
        header = [str(h).strip() if h is not None else f"列{i + 1}" for i, h in enumerate(header)]
        last: list = [None] * len(header)
        for row in rows:
            if row is None or all(v is None or str(v).strip() == "" for v in row):
                continue
            cells = []
            for i, v in enumerate(row[: len(header)]):
                if v is None or str(v).strip() == "":
                    v = last[i]  # 結合セルの続き行は前の値を引き継ぐ
                else:
                    last[i] = v
                if v is not None:
                    cells.append(f"{header[i]}: {v}")
            if cells:
                lines.append(f"{ws.title} | " + " | ".join(cells))
    return "\n".join(lines)


@dataclass
class PageMap:
    """PDFの文字オフセット→ページ番号の対応表。原本を開くリンク生成に使う。"""
    boundaries: list[int]  # boundaries[i] = i ページ目までの累積文字数

    def page_of(self, char_offset: int) -> int:
        for i, b in enumerate(self.boundaries):
            if char_offset < b:
                return i + 1
        return len(self.boundaries)


def extract_pdf_text(path: Path) -> tuple[str, PageMap]:
    pdf = pdfium.PdfDocument(str(path))
    texts = []
    boundaries = []
    total = 0
    for i in range(len(pdf)):
        page = pdf[i]
        tp = page.get_textpage()
        t = normalize_article_lines(tp.get_text_range())
        texts.append(t)
        total += len(t) + 1  # 改行区切り分を含む
        boundaries.append(total)
    return "\n".join(texts), PageMap(boundaries)


@dataclass
class DocEntry:
    doc_id: str
    filename: str
    display_name: str
    kind: str  # "docx" or "pdf"
    page_map: PageMap | None
    n_chunks: int = 0


# 主語省略(ゼロ照応)対策: チャンクが直接言及していない「主体」を、同じ文書の直近チャンクから引き継ぐ。
# KotobaCoreの共参照解決(canonical_id)は文書全体の解析結果にしか現れないため、アプリ側でチャンクに注入する。
SUBJECT_ENTITY_TYPES = {"ORGANIZATION", "PERSON"}
SUBJECT_CARRY_WINDOW = 3  # 直近何チャンクまで主体を引き継ぐか(規程集の冒頭の発行元名が延々と付かないよう制限)
EMBED_CACHE_VERSION = "v2"  # 埋め込み入力にsubject hintを含めたため旧キャッシュと区別する


def _subject_hints(result) -> list[str | None]:
    ent_by_id = {e.id: e for e in result.entities}

    def canonical_surface(e) -> str:
        rep = ent_by_id.get(e.canonical_id) if getattr(e, "canonical_id", None) else None
        return (rep or e).surface

    hints: list[str | None] = []
    carry: str | None = None
    since = 0
    for c in result.document_chunks:
        subjects: list[str] = []
        for eid in c.entity_ids:
            e = ent_by_id.get(eid)
            if e is not None and e.type in SUBJECT_ENTITY_TYPES:
                s = canonical_surface(e)
                if s not in subjects:
                    subjects.append(s)
        if subjects:
            carry, since = subjects[0], 0
            hints.append(subjects[0])
        elif carry is not None and since < SUBJECT_CARRY_WINDOW:
            since += 1
            hints.append(carry)
        else:
            hints.append(None)
    return hints


def _hinted_text(text: str, hint: str | None) -> str:
    if hint and hint not in text:
        return f"【主体: {hint}】{text}"
    return text


EMBED_BATCH_SIZE = 8  # CPUのみのbge-m3はチャンクをまとめて渡すとタイムアウトしやすいため小分けにする


def _ollama_embed_batch(texts: list[str]) -> list[list[float]]:
    payload = {"model": EMBED_MODEL, "input": texts}
    req = urllib.request.Request(
        OLLAMA_EMBED_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["embeddings"]


def ollama_embed(texts: Sequence[str]) -> list[list[float]]:
    texts = list(texts)
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(_ollama_embed_batch(texts[i : i + EMBED_BATCH_SIZE]))
    return vectors


CACHE_PATH = Path(__file__).parent / "embed_cache.json"


def _file_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class RagIndex:
    """資料の埋め込みはSHA256ハッシュでキャッシュし、内容が変わっていない資料は
    起動のたびにOllamaへ再送信しない(資料の鮮度管理・2026-09-14設計の応用)。"""

    def __init__(self, use_embeddings: bool = True) -> None:
        self.analyzer = Analyzer()
        self.use_embeddings = use_embeddings
        # embedはsearch()内でクエリ文を埋め込むために必要。チャンク側の埋め込みは
        # index()を使わずキャッシュ経由で_itemsに直接詰めるため、ここでの指定はクエリ用。
        self.retriever = InMemoryRetriever(embed=ollama_embed if use_embeddings else None)
        self.docs: dict[str, DocEntry] = {}
        self._item_hints: list[str | None] = []  # retriever._items と同じ並びのsubject hint
        self._cache: dict = {}
        if CACHE_PATH.exists():
            try:
                self._cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _index_with_optional_cache(self, doc_id: str, file_hash: str, result) -> int:
        from kotobacore.rag.features import ChunkView
        from kotobacore.rag.retriever import IndexedChunk

        chunks = result.document_chunks
        hints = _subject_hints(result)
        texts = [_hinted_text(c.text, h) for c, h in zip(chunks, hints)]
        vectors: list[list[float]] = []
        if self.use_embeddings and chunks:
            cache_key = f"{file_hash}:{EMBED_CACHE_VERSION}"
            cached = self._cache.get(cache_key)
            if cached and len(cached["vectors"]) == len(chunks):
                vectors = cached["vectors"]
            else:
                vectors = ollama_embed(texts)
                self._cache[cache_key] = {"filename": doc_id, "vectors": vectors}
        for i, c in enumerate(chunks):
            view = ChunkView.from_chunk(c, result)
            if hints[i]:
                view.text = f"{c.context}\n{texts[i]}" if c.context else texts[i]
            self._item_hints.append(hints[i])
            self.retriever._items.append(IndexedChunk(doc_id, c, view, vectors[i] if vectors else []))
        return len(chunks)

    def build(self, docs_dir: Path = DOCS_DIR) -> int:
        total_chunks = 0
        for path in sorted(docs_dir.iterdir()):
            if path.name.startswith("."):
                continue
            suffix = path.suffix.lower()
            if suffix == ".docx":
                text = extract_docx_text(path)
                page_map = None
                kind = "docx"
            elif suffix == ".pdf":
                text, page_map = extract_pdf_text(path)
                kind = "pdf"
            elif suffix == ".xlsx":
                text = extract_xlsx_text(path)
                page_map = None
                kind = "xlsx"
            else:
                continue
            if not text.strip():
                continue
            doc_id = path.stem
            file_hash = _file_hash(path)
            result = self.analyzer.analyze_document(text, document_id=doc_id)
            n = self._index_with_optional_cache(doc_id, file_hash, result)
            total_chunks += n
            self.docs[doc_id] = DocEntry(
                doc_id=doc_id, filename=path.name, display_name=path.stem, kind=kind, page_map=page_map, n_chunks=n
            )
        if self.use_embeddings:
            CACHE_PATH.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        return total_chunks

    def _hits_for(self, question: str, k: int):
        return self.retriever.search(self.analyzer.analyze_query(question), k=k)

    def search(self, question: str, k: int = 5):
        from kotobacore.core.syntax import split_clauses
        from kotobacore.rag import rrf_fuse

        # 複合条件の質問はKotobaCoreの節分割で分け、節ごとの検索結果をRRFで統合する。
        # 質問全体のベクトルが複数の話題の中間に落ちて肝心の条文を取りこぼす対策(検証D8で判明)。
        pool = k * 2
        rankings = [self._hits_for(question, pool)]
        clauses = [question[c.start : c.end].strip() for c in split_clauses(question)]
        clauses = [c for c in clauses if len(c) >= MIN_CLAUSE_CHARS]
        if len(clauses) >= 2:
            rankings.extend(self._hits_for(c, pool) for c in clauses)

        best = {}
        for hits in rankings:
            for h in hits:
                key = (h.doc_id, h.chunk_id)
                if key not in best or h.score > best[key].score:
                    best[key] = h
        if len(rankings) > 1:
            order = rrf_fuse(*[[(h.doc_id, h.chunk_id) for h in hits] for hits in rankings])
        else:
            order = [(h.doc_id, h.chunk_id) for h in rankings[0]]

        sources = []
        for key in order[:k]:
            h = best[key]
            entry = self.docs.get(h.doc_id)
            if entry is None:
                continue
            idx = next((i for i, it in enumerate(self.retriever._items) if (it.doc_id, it.chunk.id) == key), None)
            page = None
            hint = None
            if idx is not None:
                item = self.retriever._items[idx]
                hint = self._item_hints[idx]
                if entry.kind == "pdf" and entry.page_map is not None:
                    page = entry.page_map.page_of(item.chunk.begin)
            sources.append(
                {
                    "doc": entry.display_name,
                    "file": entry.filename,
                    "snippet": h.text.strip().replace("\n", " ")[:120],
                    "full_text": _hinted_text(h.text, hint),  # LLMに渡す本文にだけ主体を補う
                    "page": page,
                    "score": h.score,
                }
            )
        return sources

    def corpus_overview(self, question: str):
        """集計・一覧の質問はチャンク検索に流さず、資料のメタデータから直接答える。
        RAGが原理的に苦手な型(検証G節: k件の断片から「5件です」と誤答した)への対処。
        判別はKotobaCoreのQueryIR(answer_type LIST/NUMBER + 対象語)を使う。"""
        ir = self.analyzer.analyze_query(question)
        target = ir.target or ""
        if ir.answer_type not in ("LIST", "NUMBER") or not any(n in target for n in CORPUS_NOUNS):
            return None
        entries = list(self.docs.values())
        lines = [f"{i + 1}. {e.display_name}（{e.kind}、{e.n_chunks}チャンク）" for i, e in enumerate(entries)]
        head = "登録されている資料は全部で" if ir.answer_type == "NUMBER" else "登録されている資料は次の"
        answer = f"{head}{len(entries)}件です。\n" + "\n".join(lines)
        sources = [
            {"doc": e.display_name, "file": e.filename, "snippet": f"{e.kind}・{e.n_chunks}チャンク", "full_text": "", "page": None, "score": None}
            for e in entries
        ]
        return answer, sources


MIN_CLAUSE_CHARS = 8  # これより短い節は検索クエリとして意味を持たないので分解対象にしない
CORPUS_NOUNS = ("社内規程", "資料", "規程", "規定", "規則", "文書", "ファイル", "マニュアル")  # 「資料一覧」系と判定する対象語


def build_prompt(question: str, sources: list[dict], history: list[dict]) -> list[dict]:
    context_block = "\n\n".join(
        f"[資料{i+1}: {s['doc']}" + (f" {s['page']}ページ目" if s.get("page") else "") + f"]\n{s['full_text']}"
        for i, s in enumerate(sources)
    )
    system = (
        "あなたは社内資料に基づいて質問に答えるアシスタントです。"
        "以下の「参照資料」の内容に基づいて、日本語で簡潔に答えてください。"
        "資料に明確な記載がある場合は、それを根拠に具体的に答えてください。"
        "関連する記載が資料に見当たらない場合に限り、憶測で補わず「資料からは判断できません」と答えてください。"
        "個別の事案の該当性判断など、規程の記載を超える判断が必要な質問には、"
        "「個別の判断は人事部・専門家にご確認ください」と案内してください。\n\n"
        f"# 参照資料\n{context_block}"
    )
    messages = [{"role": "system", "content": system}]

    budget = MAX_HISTORY_CHARS
    trimmed_history = []
    for turn in reversed(history):
        cost = len(turn.get("question", "")) + len(turn.get("answer", ""))
        if budget - cost < 0:
            break
        budget -= cost
        trimmed_history.append(turn)
    for turn in reversed(trimmed_history):
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})

    messages.append({"role": "user", "content": question})
    return messages


def ask_ollama(messages: list[dict], model: str = DEFAULT_MODEL) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,  # Qwen3のthinkingモードで英語思考ログが混入するのを防ぐ(2026-09実測で判明)
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["message"]["content"].strip()
