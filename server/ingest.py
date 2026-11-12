"""CLI: python ingest.py 파일.pdf|.docx|.txt --family "TR CU" --code "TR CU 032/2013" --lang en [--version 2024-03]
같은 파일(해시 동일)은 무시. 새 파일이면 active→previous, 기존 previous는 삭제(최대 2개 보존).
API(app.py)도 ingest_file()을 그대로 사용."""
import argparse, hashlib, os, re
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
