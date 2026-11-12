"""CLI: python ingest.py 파일.pdf|.txt --family "TR CU" --code "TR CU 032/2013" --lang ru [--version 2024-03]
같은 파일(해시 동일)은 무시. 새 파일이면 active→previous, 기존 previous는 삭제(최대 2개 보존).
API(app.py)도 ingest_file()을 그대로 사용."""
import argparse, hashlib, os, re, shutil
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from pypdf import PdfReader

load_dotenv()
ART = re.compile(r"^\s*(Article|Статья|제\s*\d+\s*조|Приложение|Annex)\s*\d*", re.I)
MAX = 800  # 청크 최대 글자 수
DATA = os.path.join(os.path.dirname(__file__), "data")


def pages(path):
    if path.lower().endswith(".pdf"):
        return [(i + 1, p.extract_text() or "") for i, p in enumerate(PdfReader(path).pages)]
    return [(1, open(path, encoding="utf-8").read())]


def chunks(pgs):
    """(article, page, text). 조항 제목 줄을 만나면 새 청크, 800자 넘으면 분할."""
    art, buf, start = None, "", 1
    for pg, text in pgs:
        for line in text.splitlines():
            if not line.strip():
                continue
            if ART.match(line) or len(buf) + len(line) > MAX:
                if buf.strip():
                    yield art, start, buf.strip()
                buf, start = "", pg
                if ART.match(line):
                    art = line.strip()[:80]
            buf += line + "\n"
    if buf.strip():
        yield art, start, buf.strip()


def ingest_file(model, path, family, code, lang=None, version=None, url=None):
    """적재 후 chunk 수 반환. 문제가 있으면 ValueError(메시지는 사용자에게 보여도 됨)."""
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    items = list(chunks(pages(path)))
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
    # 원본 보관 (근거 PDF 표시용). previous 삭제 시 파일 정리는 추후.
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
