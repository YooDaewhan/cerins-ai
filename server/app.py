"""실행: uvicorn app:app --host 127.0.0.1 --port 8000"""
import os
from typing import Literal, Optional
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI
from google import genai
from google.genai import types
from pgvector.psycopg import register_vector
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

load_dotenv()
gem = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
PARSE_MODEL = os.getenv("PARSE_MODEL", "gemini-flash-lite-latest")
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gemini-flash-latest")
emb = SentenceTransformer("BAAI/bge-m3", device="cpu")
app = FastAPI()


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
    search_text: str                       # 규정 본문 검색용 문장 (규정 언어 용어 포함)


PARSE_PROMPT = ("사용자 질문을 규정 검색용 JSON으로 변환하라. 질문에 없는 값(특히 단위)은 절대 추정하지 말고 null로 둔다.\n질문: ")
ANSWER_PROMPT = """너는 인증 규정 검토 보조자다. 아래 '규정 발췌'에 있는 내용만 근거로 한국어로 답하라.
형식: 1) 결론(해당/비해당/판단불가) 2) 근거 조항(규정코드·조항·페이지) 3) 미흡하거나 추가로 필요한 정보.
수치 비교는 비교한 두 값을 명시하라. 단위가 null이거나 발췌로 판단할 수 없으면 추측하지 말고 필요한 정보를 되물어라.

질문: {q}
추출된 조건: {p}
규정 발췌:
{ctx}"""


class Ask(BaseModel):
    question: str


def search(p: Parsed, k=5):
    sql = ("SELECT d.code, c.article, c.page, c.content FROM chunks c JOIN documents d ON d.id=c.document_id "
           "WHERE d.status='active' AND (%s::text IS NULL OR d.code ILIKE %s) "
           "ORDER BY c.embedding <=> %s LIMIT %s")
    v = emb.encode(p.search_text, normalize_embeddings=True)
    like = f"{p.regulation}%" if p.regulation else None
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        register_vector(conn)
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")  # 필터 시 결과 부족 방지
        return conn.execute(sql, (like, like, v, k)).fetchall()


@app.post("/api/ask")
def ask(body: Ask):
    r = gem.models.generate_content(
        model=PARSE_MODEL, contents=PARSE_PROMPT + body.question,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Parsed))
    p: Parsed = r.parsed
    rows = search(p)
    ctx = "\n---\n".join(f"[{c} | {a} | p.{pg}]\n{t}" for c, a, pg, t in rows) or "(검색 결과 없음)"
    out = gem.models.generate_content(
        model=ANSWER_MODEL, contents=ANSWER_PROMPT.format(q=body.question, p=p.model_dump_json(), ctx=ctx),
        config=types.GenerateContentConfig(max_output_tokens=1200))
    return {"parsed": p, "sources": [{"code": c, "article": a, "page": pg} for c, a, pg, _ in rows],
            "answer": out.text}
