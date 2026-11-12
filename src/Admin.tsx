import { useCallback, useEffect, useState } from 'react'
import { api, jsonInit } from './api'

type Doc = { id: number; code: string; version_label: string | null; status: string; created_at: string }
type Term = { id: number; term: string; aliases: string[]; note: string | null }

// 파일명 → 규정 코드/언어. 예: "TRCU 032_mandatory list.docx" → "TR CU 032/2013 mandatory list"
const YEAR: Record<string, string> = { '004': '2011', '010': '2011', '012': '2011', '020': '2011', '032': '2013', '037': '2016', '043': '2017' }
function guess(name: string): { code: string; lang: string } | null {
  const base = name.replace(/\.[^.]+$/, '')
  if (/한$/.test(base)) return null // 한글 번역본은 영문판과 내용이 중복되어 제외
  const n = base.match(/(\d{3})/)?.[1]
  if (n && YEAR[n]) {
    return { code: `TR CU ${n}/${YEAR[n]}${/mandatory/i.test(base) ? ' mandatory list' : ''}`, lang: 'en' }
  }
  return /[가-힣]/.test(base) ? { code: `TR CU 참고자료 ${base}`, lang: 'ko' } : null
}

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

  // 파일을 순서대로 올림. 코드를 비워 두면 파일명에서 추정(guess), 추정 불가면 건너뜀.
  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const files = (e.currentTarget.elements.namedItem('file') as HTMLInputElement).files!
    const typed = String(f.get('code')).trim()
    setBusy(true)
    const log: string[] = []
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const g = guess(file.name)
      const code = typed && files.length === 1 ? typed : g?.code
      if (!code) { log.push(`건너뜀: ${file.name}`); setMsg(log.join('\n')); continue }
      setMsg([...log, `(${i + 1}/${files.length}) ${file.name} 임베딩 중…`].join('\n'))
      const fd = new FormData()
      fd.append('family', family); fd.append('code', code); fd.append('file', file)
      fd.append('lang', g?.lang ?? String(f.get('lang'))); fd.append('version', String(f.get('version') ?? ''))
      try {
        const r = await api<{ message: string }>('/api/regulations', { method: 'POST', body: fd })
        log.push(`${file.name} → ${r.message}`)
      } catch (err) {
        log.push(`${file.name} → 오류: ${(err as Error).message}`)
      }
      setMsg(log.join('\n'))
    }
    setBusy(false)
    load()
  }

  return (
    <div className="page">
      <h2>규정 업데이트</h2>
      <p>새 파일을 올리면 자동으로 임베딩되며, 현재 버전과 직전 버전만 보관됩니다.</p>
      <form className="form" onSubmit={upload}>
        <input name="file" type="file" accept=".pdf,.docx,.txt" multiple required />
        <input name="code" placeholder="규정 코드 (파일 1개일 때만. 비우면 파일명에서 자동 추정)" />
        <input name="version" placeholder="버전/개정일 (선택, 예: 2024-03)" />
        <select name="lang" defaultValue="en">
          <option value="en">영어</option>
          <option value="ru">러시아어</option>
          <option value="ko">한국어</option>
        </select>
        <button className="btn" disabled={busy}>
          업로드
        </button>
        {msg && <div className="meta" style={{ whiteSpace: 'pre-wrap' }}>{msg}</div>}
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
