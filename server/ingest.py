"""CLI: python ingest.py 파일.pdf|.docx|.txt --family "TR CU" --code "TR CU 032/2013" --lang en [--version 2024-03]
같은 파일(해시 동일)은 무시. 새 파일이면 active→previous, 기존 previous는 삭제(최대 2개 보존).
API(app.py)도 ingest_file()을 그대로 사용."""
import argparse, hashlib, os, re, shutil
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


def ingest_file(model, path, family, code, lang=None, version=None, url=None):
    """적재 후 chunk 수 반환. 문제가 있으면 ValueError(메시지는 사용자에게 보여도 됨)."""
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    items = list(chunks(blocks(path)))
    if not items:
        raise ValueError("텍스트를 추출하지 못했습니다 (스캔본이면 OCR 필요)")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:  # 블록 종료 시 commit, 예외면 rollback
        register_vector(conn)
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
    # 원본 보관 (근거 표시용). previous 삭제 시 파일 정리는 추후.
    d = os.path.join(DATA, re.sub(r"\W+", "_", code))
    os.makedirs(d, exist_ok=True)
    shutil.copy(path, os.path.join(d, h + os.path.splitext(path)[1]))
    return len(items)


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
