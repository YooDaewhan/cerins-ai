"""CLI: python ingest.py 파일.pdf|.docx|.txt --family "TR CU" --code "TR CU 032/2013" --lang en [--version 2024-03]
같은 파일(해시 동일)은 무시. 새 파일이면 active→previous, 기존 previous는 삭제(최대 2개 보존).
API(app.py)도 ingest_file()을 그대로 사용."""
import argparse, hashlib, json, os, re, shutil
from collections import Counter
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from pypdf import PdfReader

load_dotenv()
# 큰 제목: "Article 1. ...", "I. Scope of application", "Appendix No. 1", "Приложение", "Статья"
HEAD = re.compile(r"^(Article\s+\d+|[IVX]{1,5}\.\s+[A-Z]|Appendix|Annex|Приложение|Статья|Раздел)", re.I)
CLAUSE = re.compile(r"^(\d+(?:\.\d+)*)[\.\)]\s")  # "12. ..." 번호 조항
MAX = 1000       # 조항 하나가 이보다 길면 분할
ROW_MAX = 1500   # 표의 행 하나가 이보다 길면 분할
DATA = os.path.join(os.path.dirname(__file__), "data")


def blocks(path):
    """(page, kind, text). kind: 'p' 문단/줄, 'row' 표의 한 행. docx/txt는 page=None."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        for i, pg in enumerate(PdfReader(path).pages):
            for line in (pg.extract_text() or "").splitlines():
                yield i + 1, "p", line.strip()
    elif ext == ".docx":
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(path)
        for el in doc.element.body.iterchildren():  # 본문 순서(문단·표 섞임) 유지
            if el.tag.endswith("}p"):
                yield None, "p", Paragraph(el, doc).text.strip()
            elif el.tag.endswith("}tbl"):
                for row in Table(el, doc).rows:
                    seen, cells = set(), []
                    for c in row.cells:  # 병합 셀은 같은 셀이 반복되므로 한 번만
                        if c._tc not in seen:
                            seen.add(c._tc)
                            cells.append(" ".join(c.text.split()))
                    text = " | ".join(x for x in cells if x)
                    if text and not re.fullmatch(r"[\d\s|]+", text):  # "1 | 2 | 3 | 4" 열 번호 행 제외
                        yield None, "row", text
    else:
        for line in open(path, encoding="utf-8").read().splitlines():
            yield None, "p", line.strip()


def chunks(blks):
    """(label, page, text). 번호 조항 단위, 표는 행 단위. 라벨은 '큰제목 §조항번호'."""
    section, label, buf, start = "", "", "", None

    def flush():
        nonlocal buf
        out = (label, start, f"[{label}]\n{buf}") if buf.strip() and label else (label, start, buf)
        buf = ""
        return out if out[2].strip() else None

    for page, kind, text in blks:
        if not text:
            continue
        # 개정 이력 주석("... See previous version") 제거
        if text.startswith("(") and "See previous version" in text and len(text) < 600:
            continue
        if kind == "row":
            if (f := flush()): yield f
            tl = f"{section} (table)".strip()
            for i in range(0, len(text), ROW_MAX):
                yield tl, page, f"[{tl}]\n{text[i:i + ROW_MAX]}"
        elif HEAD.match(text):
            if (f := flush()): yield f
            section = label = text[:80]
            start = page
        elif (m := CLAUSE.match(text)):
            if (f := flush()): yield f
            label = f"{section} §{m.group(1)}".strip()
            buf, start = text, page
        else:
            if len(buf) + len(text) > MAX and (f := flush()):
                yield f
                start = page
            buf += ("\n" if buf else "") + text
            if start is None:
                start = page
    if (f := flush()): yield f


def detect(path, filename, family):
    """규정 코드·언어 자동 판단. 파일명에 3자리 번호(032)가 있으면 '<family> 032/2013'
    (연도는 본문에서 가장 많이 나온 '032/xxxx'), 없으면 파일명 그대로. 한글 문서는 ' (KO)'."""
    stem = os.path.splitext(filename)[0].strip()
    text = "\n".join(t for _, _, t in blocks(path))
    n = max(len(text), 1)
    ko = len(re.findall(r"[가-힣]", text)) / n
    ru = len(re.findall(r"[А-Яа-яЁё]", text)) / n
    lang = "ko" if ko > 0.1 else "ru" if ru > 0.3 else "en"
    m = re.search(r"(?<!\d)(\d{3})(?!\d)", stem)
    if not m:
        return stem, lang  # 번호 없는 참고자료 등
    num = m.group(1)
    years = Counter(re.findall(rf"(?<!\d){num}/(\d{{4}})", text))
    code = f"{family} {num}" + (f"/{years.most_common(1)[0][0]}" if years else "")
    if "mandatory" in stem.lower():
        code += " mandatory list"
    if lang == "ko":
        code += " (KO)"
    return code, lang


def ingest_file(model, path, family, code, lang=None, version=None, url=None):
    """적재 후 chunk 수 반환. 문제가 있으면 ValueError(메시지는 사용자에게 보여도 됨)."""
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    items = list(chunks(blocks(path)))
    if not items:
        raise ValueError("텍스트를 추출하지 못했습니다 (스캔본이면 OCR 필요)")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:  # 블록 종료 시 commit, 예외면 rollback
        register_vector(conn)
        return store_chunks(conn, model, items, h, family, code, lang, version, url)


def store_chunks(conn, model, items, h, family, code, lang, version, url):
    """items=(article, page, text) 목록을 문서 1건으로 저장. 같은 코드는 현재·직전 버전만 유지."""
    if conn.execute("SELECT 1 FROM documents WHERE code=%s AND file_hash=%s", (code, h)).fetchone():
        raise ValueError("이미 적재된 파일입니다 (변경 없음)")
    vecs = model.encode([t for _, _, t in items], batch_size=16, normalize_embeddings=True)
    conn.execute("DELETE FROM documents WHERE code=%s AND status='previous'", (code,))
    conn.execute("UPDATE documents SET status='previous' WHERE code=%s AND status='active'", (code,))
    doc_id = conn.execute(
        "INSERT INTO documents (family,code,language,version_label,source_url,file_hash)"
        " VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (family, code, lang, version, url, h)).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (document_id,article,page,content,embedding) VALUES (%s,%s,%s,%s,%s)",
            [(doc_id, art, pg, t, v) for (art, pg, t), v in zip(items, vecs)])
    return len(items)  # 원본 파일 보관은 호출 측(app.py의 업로드 기록)에서 처리


# ---------- HS 코드 ----------
# 관세청 HS부호 엑셀(10단위 한글·영문 품명) + UN Comtrade 참조표 JSON(류·호·소호 2/4/6단위 영문 이름)을 합쳐 한 트리로 만든다.
# 두 파일은 data/hs/에 보관하고, 둘 다 있으면 다시 적재한다(어느 쪽을 새로 올려도 됨).
HS_DIR = os.path.join(DATA, "hs")
HS_FILES = {".json": "comtrade.json", ".xlsx": "hsk.xlsx"}
HS_TABLE = """CREATE TABLE IF NOT EXISTS hs_codes (
    code text PRIMARY KEY, parent text, name_ko text, name_en text);
CREATE INDEX IF NOT EXISTS hs_codes_parent ON hs_codes (parent)"""


def hsk_rows(path):
    """관세청 엑셀 → {code: (한글, 영문)}. A열 HS부호(7~10자리, 7~9자리는 중간 묶음), D 한글품목명, E 영문품목명."""
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True).active
    out = {}
    for r in ws.iter_rows(min_row=2, max_col=5, values_only=True):
        code = str(r[0] or "").strip()
        if code.isdigit() and len(code) >= 7:
            out[code] = ((r[3] or "").strip(), (r[4] or "").strip())
    return out


def comtrade_names(path):
    """Comtrade 참조표 → {code: 영문}. 2·4·6단위만. 파일이 배열이든 {"results": [...]}든 받음."""
    d = json.load(open(path, encoding="utf-8-sig"))
    out = {}
    for x in d["results"] if isinstance(d, dict) else d:
        code = str(x.get("id", ""))
        if code.isdigit() and len(code) in (2, 4, 6):
            out[code] = x["text"].split(" - ", 1)[-1].lstrip("- ").strip()  # "848110 - -  Valves..." → "Valves..."
    return out


def hs_tree(hsk, names):
    """[(code, parent, ko, en)]. 부모 = 목록에 있는 가장 긴 앞자리 코드.
    Comtrade는 여러 HS 버전이 섞여 있으므로 관세청 코드의 앞자리인 것(현행)만 쓴다."""
    live = {c[:n] for c in hsk for n in (2, 4, 6)}
    nodes = {c: (None, en) for c, en in names.items() if c in live} | hsk
    rows = []
    for c in sorted(nodes):
        parent = next((c[:n] for n in range(len(c) - 1, 1, -1) if c[:n] in nodes), None)
        rows.append((c, parent, *nodes[c]))
    return rows


def hs_upload(model, path, filename, family):
    """업로드 파일을 보관하고, 엑셀·JSON이 둘 다 있으면 적재. (메시지, 조각 수) 반환."""
    suffix = os.path.splitext(filename)[1].lower()
    # 형식부터 확인해 엉뚱한 파일이 보관본을 덮어쓰지 않게 함
    n = len(hsk_rows(path)) if suffix == ".xlsx" else len(comtrade_names(path))
    if not n:
        raise ValueError("HS 코드를 찾지 못했습니다 (관세청 HS부호 엑셀 또는 Comtrade 참조표 JSON인지 확인)")
    os.makedirs(HS_DIR, exist_ok=True)
    shutil.copy(path, os.path.join(HS_DIR, HS_FILES[suffix]))
    if suffix == ".xlsx":
        with open(os.path.join(HS_DIR, "hsk.name"), "w", encoding="utf-8") as f:
            f.write(filename)
    if not all(os.path.exists(os.path.join(HS_DIR, f)) for f in HS_FILES.values()):
        return f"{n}개 코드 저장 — 나머지 파일(관세청 엑셀 / Comtrade JSON)도 올리면 적재됩니다", 0
    return hs_rebuild(model, family)


HS_MAX = 1500       # 조각 최대 글자 수. 하위 코드가 많은 호는 소호 단위로 나눠 임베딩 시간(긴 문장일수록 급증)을 줄임
HS_CHUNKING = b"v2"  # 조각 나누는 방식이 바뀌면 올려서, 같은 파일이라도 다시 적재되게 함


def hs_rebuild(model, family="HS"):
    """data/hs/의 엑셀·JSON으로 hs_codes와 질문하기용 조각을 다시 만든다."""
    xlsx, js = (os.path.join(HS_DIR, HS_FILES[s]) for s in (".xlsx", ".json"))
    rows = hs_tree(hsk_rows(xlsx), comtrade_names(js))
    by = {c: (ko, en) for c, _, ko, en in rows}
    label = lambda c: f"{c} {' / '.join(x for x in by[c] if x)}"
    kids = {}
    for c, p, *_ in rows:
        kids.setdefault(p, []).append(c)

    def lines(c, depth):
        return [(c, ("  " * depth) + label(c))] + [x for k in kids.get(c, []) for x in lines(k, depth + 1)]

    # 질문하기용 조각: 호(4단위)마다 류·호 이름 + 하위 코드. 길면 줄 단위로 나누고 article은 첫 소호(6단위)
    items = []
    for c, p, *_ in rows:
        if len(c) != 4 or not p:
            continue
        head = f"[HS {c}] 류 {label(p)}\n{label(c)}"
        pieces, cur = [], []
        for code, text in lines(c, 0)[1:]:
            if cur and sum(len(t) + 1 for _, t in cur) + len(text) > HS_MAX:
                pieces.append(cur)
                cur = []
            cur.append((code, text))
        pieces.append(cur)
        for pc in pieces:
            art = c if len(pieces) == 1 or not pc else pc[0][0][:6]
            items.append((art, None, "\n".join([head, *(t for _, t in pc)])))
    ver = (re.findall(r"\d{8}", open(os.path.join(HS_DIR, "hsk.name"), encoding="utf-8").read()) or [None])[0]
    h = hashlib.sha256(open(xlsx, "rb").read() + open(js, "rb").read() + HS_CHUNKING).hexdigest()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        register_vector(conn)
        n = store_chunks(conn, model, items, h, family, "HSK", "ko", ver, None)
        conn.execute("DELETE FROM hs_codes")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO hs_codes (code, parent, name_ko, name_en) VALUES (%s,%s,%s,%s)", rows)
    return f"HS 코드 {len(rows)}개, {n}개 조각 적재", n


if __name__ == "__main__":
    from sentence_transformers import SentenceTransformer
    a = argparse.ArgumentParser()
    a.add_argument("file"); a.add_argument("--family", required=True)
    a.add_argument("--code", required=True); a.add_argument("--lang")
    a.add_argument("--version"); a.add_argument("--url")
    a = a.parse_args()
    try:
        n = ingest_file(SentenceTransformer("BAAI/bge-m3", device="cpu"),
                        a.file, a.family, a.code, a.lang, a.version, a.url)
        print(f"적재 완료: chunks={n}")
    except ValueError as e:
        raise SystemExit(str(e))
