"""사용: python ingest.py 파일.pdf|.txt --family "TR CU" --code "TR CU 032/2013" --lang ru [--version 2024-03]
같은 파일(해시 동일)은 무시. 새 파일이면 active→previous, 기존 previous는 삭제(최대 2개 보존)."""
import argparse, hashlib, os, re, shutil
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

load_dotenv()
ART = re.compile(r"^\s*(Article|Статья|제\s*\d+\s*조|Приложение|Annex)\s*\d*", re.I)
MAX = 800  # 청크 최대 글자 수


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


def main():
    a = argparse.ArgumentParser()
    a.add_argument("file"); a.add_argument("--family", required=True)
    a.add_argument("--code", required=True); a.add_argument("--lang")
    a.add_argument("--version"); a.add_argument("--url")
    a = a.parse_args()

    raw = open(a.file, "rb").read()
    h = hashlib.sha256(raw).hexdigest()
    items = list(chunks(pages(a.file)))
    if not items:
        raise SystemExit("텍스트를 추출하지 못함 (스캔본이면 OCR 필요)")

    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    vecs = model.encode([t for _, _, t in items], batch_size=16,
                        normalize_embeddings=True, show_progress_bar=True)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:  # 블록 종료 시 commit, 예외면 rollback
        register_vector(conn)
        if conn.execute("SELECT 1 FROM documents WHERE code=%s AND file_hash=%s", (a.code, h)).fetchone():
            raise SystemExit("이미 적재된 파일 (변경 없음)")
        conn.execute("DELETE FROM documents WHERE code=%s AND status='previous'", (a.code,))
        conn.execute("UPDATE documents SET status='previous' WHERE code=%s AND status='active'", (a.code,))
        doc_id = conn.execute(
            "INSERT INTO documents (family,code,language,version_label,source_url,file_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (a.family, a.code, a.lang, a.version, a.url, h)).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (document_id,article,page,content,embedding) VALUES (%s,%s,%s,%s,%s)",
                [(doc_id, art, pg, t, v) for (art, pg, t), v in zip(items, vecs)])
    # 원본 보관 (근거 PDF 표시용). previous 삭제 시 파일 정리는 추후.
    d = os.path.join(os.path.dirname(__file__), "data", re.sub(r"\W+", "_", a.code))
    os.makedirs(d, exist_ok=True)
    shutil.copy(a.file, os.path.join(d, h + os.path.splitext(a.file)[1]))
    print(f"적재 완료: document_id={doc_id}, chunks={len(items)}")


if __name__ == "__main__":
    main()
