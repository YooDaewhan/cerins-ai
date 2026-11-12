import { useCallback, useEffect, useState } from 'react'
import { api, jsonInit } from './api'

type Doc = { id: number; code: string; version_label: string | null; status: string; created_at: string }
type Term = { id: number; term: string; aliases: string[]; note: string | null }

// 목록 조회 + 변경 후 새로고침 공통 훅
function useList<T>(url: string) {
  const [rows, setRows] = useState<T[]>([])
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    api<T[]>(url).then(setRows).catch((e) => setErr(String(e.message)))
  }, [url])
  useEffect(() => {
    load()
  }, [load])
  return { rows, err, load }
}

export function Regulations({ family }: { family: string }) {
  const { rows, err, load } = useList<Doc>(`/api/regulations?family=${encodeURIComponent(family)}`)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    fd.append('family', family)
    setBusy(true)
    setMsg('업로드·임베딩 중… (수 분 걸릴 수 있음)')
    try {
      const r = await api<{ message: string }>('/api/regulations', { method: 'POST', body: fd })
      setMsg(r.message)
      load()
    } catch (e) {
      setMsg(`오류: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <h2>규정 업데이트</h2>
      <p>새 파일을 올리면 자동으로 임베딩되며, 현재 버전과 직전 버전만 보관됩니다.</p>
      <form className="form" onSubmit={upload}>
        <input name="code" required placeholder="규정 코드 (예: TR CU 032/2013)" />
        <input name="version" placeholder="버전/개정일 (예: 2024-03)" />
        <select name="lang" defaultValue="ru">
          <option value="ru">러시아어</option>
          <option value="en">영어</option>
          <option value="ko">한국어</option>
        </select>
        <input name="file" type="file" accept=".pdf,.txt" required />
        <button className="btn" disabled={busy}>
          업로드
        </button>
        {msg && <div className="meta">{msg}</div>}
      </form>
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr><th>코드</th><th>버전</th><th>상태</th><th>등록일</th></tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id}>
              <td>{d.code}</td>
              <td>{d.version_label ?? '-'}</td>
              <td>{d.status === 'active' ? '현재' : '이전'}</td>
              <td>{d.created_at.slice(0, 10)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Glossary({ family }: { family: string }) {
  const { rows, err, load } = useList<Term>(`/api/glossary?family=${encodeURIComponent(family)}`)
  const [msg, setMsg] = useState('')

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const f = new FormData(form)
    const aliases = String(f.get('aliases')).split(',').map((s) => s.trim()).filter(Boolean)
    try {
      await api('/api/glossary', jsonInit('POST', { family, term: f.get('term'), aliases, note: f.get('note') || null }))
      form.reset()
      setMsg('')
      load()
    } catch (e) {
      setMsg(`오류: ${(e as Error).message}`)
    }
  }
  async function del(id: number) {
    await api(`/api/glossary/${id}`, { method: 'DELETE' })
    load()
  }

  return (
    <div className="page">
      <h2>용어 사전</h2>
      <p>사용자가 쓰는 말과 규정에서 쓰는 말이 다를 때 연결해 둡니다. 질문에 해당 표현이 있으면 검색에 함께 사용됩니다.</p>
      <form className="form" onSubmit={add}>
        <input name="term" required placeholder="사용자 표현 (예: 쌀)" />
        <input name="aliases" required placeholder="규정상 표현, 쉼표로 구분 (예: 곡식, 곡물, 미곡)" />
        <input name="note" placeholder="메모 (선택)" />
        <button className="btn">추가</button>
        {msg && <div className="err">{msg}</div>}
      </form>
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr><th>표현</th><th>연결 용어</th><th /></tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td>{t.term}</td>
              <td>{t.aliases.join(', ')}</td>
              <td><button className="link" onClick={() => del(t.id)}>삭제</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
