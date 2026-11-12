"""실행: uvicorn app:app --host 127.0.0.1 --port 8000"""
import os, re, shutil, tempfile, time
from typing import Literal, Optional
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from google.genai import errors, types
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from ingest import DATA, detect, ingest_file

load_dotenv()
PARSE_MODEL = os.getenv("PARSE_MODEL")    # .env에서 지정 (모델 이름은 직접 조회해서 정함)
ANSWER_MODEL = os.getenv("ANSWER_MODEL")
emb = SentenceTransformer("BAAI/bge-m3", device="cpu")
app = FastAPI()
_gem = None


@app.exception_handler(errors.APIError)
async def gemini_error(request, exc):
    """Gemini 호출 실패를 500 대신 안내 문구로 반환. 과부하·한도 초과는 사용자용 안내만, 그 외는 원인 표시."""
    print(f"[gemini] {request.url.path} → {exc.code} {exc.status}: {str(exc)[:200]}", flush=True)  # 원인 확인용 (화면에는 안내만)
    if exc.code in (429, 503):
        return JSONResponse({"detail": "AI 서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요."}, status_code=503)
    return JSONResponse({"detail": f"Gemini 오류: {str(exc)[:300]}"}, status_code=502)


def gem():
    """Gemini 키가 없어도 규정/용어사전 API는 동작하도록 첫 질문 때 생성."""
    global _gem
    if _gem is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise HTTPException(503, "GEMINI_API_KEY가 설정되지 않았습니다")
        if not PARSE_MODEL or not ANSWER_MODEL:
            raise HTTPException(503, ".env에 PARSE_MODEL, ANSWER_MODEL을 설정해 주세요")
        _gem = genai.Client(api_key=key)
    return _gem


def generate(model, contents, config=None):
    """Gemini 호출. 서버 과부하(503)·한도(429)는 잠시 후 3회까지 재시도, 계속 실패하면 예외(안내 문구로 변환됨)."""
    for attempt in range(3):
        try:
            return gem().models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as e:
            if e.code not in (429, 503) or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


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
                "search_text에는 일상 표현을 규정에서 쓰는 정식 용어로 바꿔 쓴다. "
                "이전 대화가 있으면 이번 질문은 그 후속이므로, 이전에 나온 수치·장비·조건도 합쳐서 추출한다. "
                "도면 사양이 첨부돼 있으면 질문에 없는 값은 도면 사양에서 채운다.")
ANSWER_PROMPT = """너는 인증 규정 검토 보조자다. 아래 '규정 발췌'에 있는 내용만 근거로 한국어로 답하라.
형식: 1) 결론(해당/비해당/판단불가) 2) 근거 조항(규정코드·조항) 3) 미흡하거나 추가로 필요한 정보. 간결하게 쓴다.
수치 비교는 비교한 두 값을 명시하라. 단위가 null이거나 발췌로 판단할 수 없으면 추측하지 말고 필요한 정보를 되물어라.

이전 대화:
{hist}
{drw}
질문: {q}
추출된 조건: {p}
규정 발췌:
{ctx}"""


class Turn(BaseModel):
    role: Literal["me", "ai"]
    text: str


class Ask(BaseModel):
    question: str
    family: Optional[str] = None
    history: list[Turn] = []   # 이전 대화 (화면이 최근 몇 개만 보냄)
    drawing: Optional[str] = None  # 첨부한 도면에서 읽은 사양 요약 (/api/drawings 결과)


# ---------- 도면 ----------
class Spec(BaseModel):
    equipment_type: Optional[str] = None
    design_pressure: Qty
    working_pressure: Qty
    capacity: Qty                 # 용량(부피)
    nominal_diameter: Qty         # 호칭지름
    design_temperature: Qty
    working_medium: Optional[str] = None
    material: Optional[str] = None
    notes: Optional[str] = None   # 읽기 어렵거나 불확실한 부분


DRAWING_PROMPT = ("첨부한 기술 도면(PDF)에서 장비 사양을 읽어 JSON으로 답하라. 도면에 없거나 읽을 수 없는 값은 반드시 null로 두고 "
                  "절대 추정하지 않는다. 숫자와 단위는 도면에 적힌 그대로 쓴다(단위를 바꾸거나 환산하지 않는다). "
                  "글자가 작거나 흐려서 확신이 없는 값은 notes에 적는다.")


def spec_text(s: Spec) -> str:
    q = lambda x: None if x.value is None else f"{x.value:g} {x.unit or '(단위 불명)'}"
    rows = [("장비", s.equipment_type), ("설계압력", q(s.design_pressure)), ("작동압력", q(s.working_pressure)),
            ("용량", q(s.capacity)), ("호칭지름", q(s.nominal_diameter)), ("설계온도", q(s.design_temperature)),
            ("작동 매체", s.working_medium), ("재질", s.material), ("메모", s.notes)]
    return "\n".join(f"{k}: {v}" for k, v in rows if v)


@app.post("/api/drawings")
def analyze_drawing(file: UploadFile = File()):
    """도면 PDF → Gemini가 사양 추출. 저장하지 않음(질문 때마다 화면이 요약 텍스트를 같이 보냄)."""
    data = file.file.read()
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 분석할 수 있습니다")
    if len(data) > 18 * 1024 * 1024:  # 인라인 전송 한도(요청 20MB)
        raise HTTPException(413, "파일이 너무 큽니다 (18MB 이하)")
    r = generate(
        model=ANSWER_MODEL,
        contents=[types.Part.from_bytes(data=data, mime_type="application/pdf"), DRAWING_PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Spec))
    s: Spec = r.parsed
    return {"spec": s, "text": spec_text(s) or "(읽을 수 있는 사양이 없습니다)"}


def history_text(h: list[Turn]) -> str:
    """최근 6개 메시지만, 각 600자까지. 후속 질문("용량은 0.01 m3예요")에서 앞 조건을 잊지 않게 함."""
    return "\n".join(f"{'사용자' if t.role == 'me' else 'AI'}: {t.text[:600]}" for t in h[-6:])


def glossary_terms(question: str, family: Optional[str]) -> list[str]:
    """질문에 포함된 사전 표현의 연결 용어를 모은다 (AI 호출 없음)."""
    with connect() as c:
        rows = c.execute("SELECT term, aliases FROM glossary WHERE (%s::text IS NULL OR family=%s)",
                         (family, family)).fetchall()
    return [a for r in rows if r["term"] in question for a in r["aliases"]]


def search(p: Parsed, family: Optional[str], extra: list[str], k=10):
    sql = ("SELECT d.code, c.article, c.page, c.content FROM chunks c JOIN documents d ON d.id=c.document_id "
           "WHERE d.status='active' AND (%s::text IS NULL OR d.family=%s) "
           "AND (%s::text[] IS NULL OR d.code ~ ANY(%s::text[])) "
           "ORDER BY c.embedding <=> %s LIMIT %s")
    v = emb.encode(" ".join([p.search_text, *extra]), normalize_embeddings=True)
    # "TR CU 032" 같은 표기는 번호(032)만 뽑아 코드와 매칭 (TR CU/TR EAEU 표기 차이 흡수)
    pats = [rf"\m{n}\M" for n in re.findall(r"\d{3}", p.regulation or "")] or None
    with connect() as c:
        c.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")  # 필터 시 결과 부족 방지
        return c.execute(sql, (family, family, pats, pats, v, k)).fetchall()


@app.post("/api/ask")
def ask(body: Ask):
    hist = history_text(body.history) or "(없음)"
    drw = f"\n도면에서 읽은 사양(사용자가 첨부, 도면에 없는 값은 되물어라):\n{body.drawing}\n" if body.drawing else ""
    r = generate(
        model=PARSE_MODEL, contents=f"{PARSE_PROMPT}\n이전 대화:\n{hist}\n{drw}\n질문: {body.question}",
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Parsed))
    p: Parsed = r.parsed
    rows = search(p, body.family, glossary_terms(body.question, body.family))
    ctx = "\n---\n".join(f"[{x['code']} | {x['article']}{' | p.' + str(x['page']) if x['page'] else ''}]\n{x['content']}"
                         for x in rows) or "(검색 결과 없음)"
    out = generate(
        model=ANSWER_MODEL, contents=ANSWER_PROMPT.format(q=body.question, p=p.model_dump_json(), ctx=ctx, hist=hist, drw=drw),
        config=types.GenerateContentConfig(max_output_tokens=4096))  # 생각 토큰 포함 한도라 넉넉히
    return {"parsed": p, "answer": out.text,
            "sources": [{"code": x["code"], "article": x["article"], "page": x["page"]} for x in rows]}


# ---------- 규정 ----------
@app.get("/api/regulations")
def list_regulations(family: str):
    with connect() as c:
        return c.execute("SELECT id, code, version_label, status, created_at::text AS created_at "
                         "FROM documents WHERE family=%s ORDER BY code, created_at DESC", (family,)).fetchall()


@app.post("/api/regulations")
def upload_regulation(family: str = Form(), batch: str = Form(), file: UploadFile = File(),
                      code: Optional[str] = Form(None), lang: Optional[str] = Form(None),
                      version: Optional[str] = Form(None)):
    """batch: 한 번의 업로드(여러 파일)를 묶는 ID. 코드·언어는 비우면 파일 내용/이름으로 자동 판단."""
    # 동기 함수라 FastAPI가 스레드풀에서 실행 → 임베딩 중에도 다른 요청은 처리됨
    name = os.path.basename(file.filename or "file")
    suffix = os.path.splitext(name)[1].lower()
    if suffix not in (".pdf", ".docx", ".txt"):
        raise HTTPException(400, "pdf, docx, txt 파일만 올릴 수 있습니다")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as t:
        t.write(file.file.read())
    code_ = code
    try:
        dcode, dlang = detect(t.name, name, family)
        code_, lang = (code or dcode).strip(), lang or dlang
        n = ingest_file(emb, t.name, family, code_, lang, version)
    except ValueError as e:
        log_ingest(batch, family, code_ or name, name, version, 0, "skipped", str(e), None)
        raise HTTPException(400, str(e))
    except Exception as e:
        log_ingest(batch, family, code_ or name, name, version, 0, "error", repr(e)[:300], None)
        raise
    else:
        log_ingest(batch, family, code_, name, version, n, "ok", None, t.name)
    finally:
        os.unlink(t.name)
    return {"message": f"{code_} ({n}개 조각)", "code": code_}


def log_ingest(batch, family, code, filename, version, chunks, status, message, src):
    """업로드 기록 + 원본 보관 (삭제되지 않음). 기록 실패가 업로드 결과를 막지 않게 무시."""
    try:
        with connect() as c:
            lid = c.execute("INSERT INTO ingest_log (batch, family, code, filename, version_label, chunks, status, message) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (batch, family, code, filename, version, chunks, status, message)).fetchone()["id"]
            if src:
                safe = re.sub(r'[\\/:*?"<>|]', "_", filename)
                rel = f"uploads/{lid}/{safe}"
                os.makedirs(os.path.dirname(os.path.join(DATA, rel)), exist_ok=True)
                shutil.copy(src, os.path.join(DATA, rel))
                c.execute("UPDATE ingest_log SET stored_path=%s WHERE id=%s", (rel, lid))
            prune_history(c, family)
    except Exception:
        pass


KEEP_BATCHES = 10  # 최근 업데이트(업로드 1회 = 1건) 보관 개수


def prune_history(c, family):
    """오래된 업데이트 기록과 보관 원본을 삭제해 최근 KEEP_BATCHES건만 남긴다."""
    old = [r["batch"] for r in c.execute(
        "SELECT batch FROM ingest_log WHERE family=%s AND batch IS NOT NULL "
        "GROUP BY batch ORDER BY max(id) DESC OFFSET %s", (family, KEEP_BATCHES)).fetchall()]
    for b in old:
        for r in c.execute("DELETE FROM ingest_log WHERE family=%s AND batch=%s RETURNING id", (family, b)).fetchall():
            shutil.rmtree(os.path.join(DATA, "uploads", str(r["id"])), ignore_errors=True)


@app.get("/api/regulations/log")
def regulation_log(family: str, limit: int = 300):
    with connect() as c:
        return c.execute("SELECT id, batch, code, filename, chunks, status, message, (stored_path IS NOT NULL) AS has_file, "
                         "created_at::text AS created_at FROM ingest_log WHERE family=%s ORDER BY id DESC LIMIT %s",
                         (family, limit)).fetchall()


@app.get("/api/regulations/file/{lid}")
def download_original(lid: int):
    with connect() as c:
        r = c.execute("SELECT filename, stored_path FROM ingest_log WHERE id=%s", (lid,)).fetchone()
    if not r or not r["stored_path"]:
        raise HTTPException(404, "원본 파일이 없습니다")
    return FileResponse(os.path.join(DATA, r["stored_path"]), filename=r["filename"])


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
