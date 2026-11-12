"""실행: uvicorn app:app --host 127.0.0.1 --port 8000"""
import os, tempfile
from typing import Literal, Optional
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from google import genai
from google.genai import types
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from ingest import ingest_file

load_dotenv()
PARSE_MODEL = os.getenv("PARSE_MODEL", "gemini-flash-lite-latest")
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gemini-flash-latest")
emb = SentenceTransformer("BAAI/bge-m3", device="cpu")
app = FastAPI()
_gem = None


def gem():
    """Gemini 키가 없어도 규정/용어사전 API는 동작하도록 첫 질문 때 생성."""
    global _gem
    if _gem is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise HTTPException(503, "GEMINI_API_KEY가 설정되지 않았습니다")
        _gem = genai.Client(api_key=key)
    return _gem


def connect():
    c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    register_vector(c)
    return c


# ---------- 질문 ----------
class Qty(BaseModel):
    value: Optional[float] = None
    kind: Optional[str] = None   # 예: 호칭지름(DN), 설계압력, 작동압력
    unit: Optional[str] = None   # 예: mm, cm, inch, bar, MPa. 질문에 없으면 null (추정 금지)


class Parsed(BaseModel):
    intent: Literal["check_applicability", "find_clause", "compare_versions", "other"]
    regulation: Optional[str] = None       # 예: "TR CU 032"
    equipment_type: Optional[str] = None
    size: Qty
    pressure: Qty
    search_text: str                       # 규정 본문 검색용 문장 (규정에서 쓰는 용어로 바꿔 쓴다)


PARSE_PROMPT = ("사용자 질문을 규정 검색용 JSON으로 변환하라. 질문에 없는 값(특히 단위)은 절대 추정하지 말고 null로 둔다. "
                "search_text에는 일상 표현을 규정에서 쓰는 정식 용어로 바꿔 쓴다.\n질문: ")
ANSWER_PROMPT = """너는 인증 규정 검토 보조자다. 아래 '규정 발췌'에 있는 내용만 근거로 한국어로 답하라.
형식: 1) 결론(해당/비해당/판단불가) 2) 근거 조항(규정코드·조항·페이지) 3) 미흡하거나 추가로 필요한 정보.
수치 비교는 비교한 두 값을 명시하라. 단위가 null이거나 발췌로 판단할 수 없으면 추측하지 말고 필요한 정보를 되물어라.

질문: {q}
추출된 조건: {p}
규정 발췌:
{ctx}"""


class Ask(BaseModel):
    question: str
    family: Optional[str] = None


def glossary_terms(question: str, family: Optional[str]) -> list[str]:
    """질문에 포함된 사전 표현의 연결 용어를 모은다 (AI 호출 없음)."""
    with connect() as c:
        rows = c.execute("SELECT term, aliases FROM glossary WHERE (%s::text IS NULL OR family=%s)",
                         (family, family)).fetchall()
    return [a for r in rows if r["term"] in question for a in r["aliases"]]


def search(p: Parsed, family: Optional[str], extra: list[str], k=5):
    sql = ("SELECT d.code, c.article, c.page, c.content FROM chunks c JOIN documents d ON d.id=c.document_id "
           "WHERE d.status='active' AND (%s::text IS NULL OR d.family=%s) "
           "AND (%s::text IS NULL OR d.code ILIKE %s) "
           "ORDER BY c.embedding <=> %s LIMIT %s")
    v = emb.encode(" ".join([p.search_text, *extra]), normalize_embeddings=True)
    like = f"{p.regulation}%" if p.regulation else None
    with connect() as c:
        c.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")  # 필터 시 결과 부족 방지
        return c.execute(sql, (family, family, like, like, v, k)).fetchall()


@app.post("/api/ask")
def ask(body: Ask):
    r = gem().models.generate_content(
        model=PARSE_MODEL, contents=PARSE_PROMPT + body.question,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Parsed))
    p: Parsed = r.parsed
    rows = search(p, body.family, glossary_terms(body.question, body.family))
    ctx = "\n---\n".join(f"[{x['code']} | {x['article']} | p.{x['page']}]\n{x['content']}" for x in rows) or "(검색 결과 없음)"
    out = gem().models.generate_content(
        model=ANSWER_MODEL, contents=ANSWER_PROMPT.format(q=body.question, p=p.model_dump_json(), ctx=ctx),
        config=types.GenerateContentConfig(max_output_tokens=1200))
    return {"parsed": p, "answer": out.text,
            "sources": [{"code": x["code"], "article": x["article"], "page": x["page"]} for x in rows]}


# ---------- 규정 ----------
@app.get("/api/regulations")
def list_regulations(family: str):
    with connect() as c:
        return c.execute("SELECT id, code, version_label, status, created_at::text AS created_at "
                         "FROM documents WHERE family=%s ORDER BY code, created_at DESC", (family,)).fetchall()


@app.post("/api/regulations")
def upload_regulation(family: str = Form(), code: str = Form(), lang: Optional[str] = Form(None),
                      version: Optional[str] = Form(None), file: UploadFile = File()):
    # 동기 함수라 FastAPI가 스레드풀에서 실행 → 임베딩 중에도 다른 요청은 처리됨
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in (".pdf", ".txt"):
        raise HTTPException(400, "pdf 또는 txt 파일만 올릴 수 있습니다")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as t:
        t.write(file.file.read())
    try:
        n = ingest_file(emb, t.name, family, code.strip(), lang, version)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        os.unlink(t.name)
    return {"message": f"완료: {code} ({n}개 조각)"}


# ---------- 용어 사전 ----------
class Term(BaseModel):
    family: str
    term: str
    aliases: list[str]
    note: Optional[str] = None


@app.get("/api/glossary")
def list_glossary(family: str):
    with connect() as c:
        return c.execute("SELECT id, term, aliases, note FROM glossary WHERE family=%s ORDER BY term", (family,)).fetchall()


@app.post("/api/glossary")
def add_glossary(t: Term):
    with connect() as c:
        c.execute("INSERT INTO glossary (family, term, aliases, note) VALUES (%s,%s,%s,%s) "
                  "ON CONFLICT (family, term) DO UPDATE SET aliases=EXCLUDED.aliases, note=EXCLUDED.note",
                  (t.family, t.term.strip(), t.aliases, t.note))
    return {"ok": True}


@app.delete("/api/glossary/{gid}")
def delete_glossary(gid: int):
    with connect() as c:
        c.execute("DELETE FROM glossary WHERE id=%s", (gid,))
    return {"ok": True}
