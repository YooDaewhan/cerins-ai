import { useCallback, useEffect, useState } from 'react'
import { api, jsonInit } from './api'

type Doc = { id: number; code: string; version_label: string | null; status: string; created_at: string }
type Log = { id: number; batch: string; code: string; filename: string | null; chunks: number; status: string; message: string | null; has_file: boolean; created_at: string }
type Term = { id: number; term: string; aliases: string[]; note: string | null }
const STATUS: Record<string, string> = { ok: '성공', skipped: '건너뜀', error: '오류' }

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

// 같은 batch(한 번의 업로드)끼리 묶기. rows는 최신순.
function groupBatches(rows: Log[]) {
  const map = new Map<string, Log[]>()
  for (const r of rows) map.set(r.batch ?? `single-${r.id}`, [...(map.get(r.batch ?? `single-${r.id}`) ?? []), r])
  return [...map.entries()].map(([id, items]) => ({ id, items }))
}

function History({ rows }: { rows: Log[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const groups = groupBatches(rows)
  if (!groups.length) return <p className="meta">아직 업데이트 기록이 없습니다.</p>
  return (
    <div>
      {groups.map((g) => {
        const ok = g.items.filter((i) => i.status === 'ok').length
        const first = g.items[g.items.length - 1]
        return (
          <div key={g.id} style={{ borderBottom: '1px solid var(--border)' }}>
            <button className="link" style={{ width: '100%', textAlign: 'left', padding: '10px 4px', color: 'inherit' }}
              onClick={() => setOpen(open === g.id ? null : g.id)}>
              {open === g.id ? '▾' : '▸'} {first.created_at.slice(0, 16)} — 파일 {g.items.length}개 (성공 {ok}
              {g.items.length - ok > 0 ? `, 그 외 ${g.items.length - ok}` : ''})
            </button>
            {open === g.id && (
              <table>
                <tbody>
                  {[...g.items].reverse().map((h) => (
                    <tr key={h.id} title={h.message ?? ''}>
                      <td>{h.code}</td>
                      <td>{STATUS[h.status] ?? h.status}{h.status === 'ok' ? ` (${h.chunks}개)` : h.message ? ` — ${h.message}` : ''}</td>
                      <td>{h.has_file ? <a href={`/api/regulations/file/${h.id}`}>{h.filename} ⬇</a> : h.filename}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function Regulations({ family }: { family: string }) {
  const { rows, err, load } = useList<Doc>(`/api/regulations?family=${encodeURIComponent(family)}`)
  const hist = useList<Log>(`/api/regulations/log?family=${encodeURIComponent(family)}`)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  // 선택한 파일을 한 번의 업데이트(batch)로 묶어 순서대로 올림. 코드·언어는 서버가 자동 판단.
  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const input = e.currentTarget.elements.namedItem('file') as HTMLInputElement
    const files = Array.from(input.files ?? [])
    const batch = Date.now().toString(36) + Math.random().toString(36).slice(2) // http에서는 crypto.randomUUID 사용 불가
    setBusy(true)
    const log: string[] = []
    for (const [i, file] of files.entries()) {
      setMsg([...log, `(${i + 1}/${files.length}) ${file.name} 처리 중…`].join('\n'))
      const fd = new FormData()
      fd.append('family', family)
      fd.append('batch', batch)
      fd.append('file', file)
      try {
        const r = await api<{ message: string }>('/api/regulations', { method: 'POST', body: fd })
        log.push(`✔ ${file.name} → ${r.message}`)
      } catch (err) {
        log.push(`✘ ${file.name} → ${(err as Error).message}`)
      }
    }
    setMsg(log.join('\n'))
    setBusy(false)
    load()
    hist.load()
  }

  return (
    <div className="page">
      <h2>규정 업데이트</h2>
      <p>파일을 선택해 올리면 자동으로 임베딩됩니다. 선택한 파일은 한 번의 업데이트로 기록되고 원본도 보관됩니다. 같은 규정은 현재와 직전 버전만 유지됩니다.</p>
      <form className="form" onSubmit={upload}>
        <input name="file" type="file" accept=".pdf,.docx,.txt" multiple required />
        <button className="btn" disabled={busy}>
          {busy ? '업로드 중…' : '업로드'}
        </button>
        {msg && <div className="meta" style={{ whiteSpace: 'pre-wrap' }}>{msg}</div>}
      </form>

      <h3>업데이트 기록 (최근 10회)</h3>
      <History rows={hist.rows} />

      <h3 style={{ marginTop: 32 }}>현재 적용 중인 규정</h3>
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr><th>코드</th><th>상태</th><th>등록일</th></tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id}>
              <td>{d.code}</td>
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
