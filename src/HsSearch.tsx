import { useEffect, useState } from 'react'
import { api } from './api'

type Row = { code: string; name_ko: string | null; name_en: string | null; has_children: boolean; heading: string | null }
type Mention = { code: string; article: string | null; page: number | null; snippet: string }
type Detail = { path: Row[]; children: Row[]; mentions: Mention[] }
type Found = { codes: Row[]; headings: Row[] }

// 관세청 표기: 8481.80-1010
const fmt = (c: string) => (c.length <= 4 ? c : `${c.slice(0, 4)}.${c.slice(4, 6)}${c.length > 6 ? '-' + c.slice(6) : ''}`)
const LEVEL: Record<number, string> = { 2: '류', 4: '호', 6: '소호' }

function Name({ r }: { r: Row }) {
  return (
    <>
      {r.name_ko || r.name_en}
      {r.name_ko && r.name_en && <div className="meta" style={{ marginTop: 0 }}>{r.name_en}</div>}
    </>
  )
}

function Rows({ rows, onPick, context }: { rows: Row[]; onPick: (c: string) => void; context?: boolean }) {
  return (
    <table>
      <tbody>
        {rows.map((r) => (
          <tr key={r.code} className="click" onClick={() => onPick(r.code)}>
            <td style={{ whiteSpace: 'nowrap', fontFamily: 'monospace' }}>{fmt(r.code)}</td>
            <td>
              <Name r={r} />
              {/* 검색 결과에서 '기타' 같은 품명도 알아보게 소속 호를 같이 표시 */}
              {context && r.code.length > 4 && r.heading && <div className="meta" style={{ marginTop: 0 }}>{r.heading}</div>}
            </td>
            <td className="meta" style={{ whiteSpace: 'nowrap' }}>{r.has_children ? '▸' : LEVEL[r.code.length] ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function HsSearch() {
  const [code, setCode] = useState<string | null>(null) // 현재 보고 있는 코드 (null = 류 목록)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [q, setQ] = useState('')
  const [found, setFound] = useState<Found | null>(null)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState('')

  useEffect(() => {
    const p = code
      ? api<Detail>(`/api/hs/code/${code}`)
      : api<Row[]>('/api/hs/children').then((children) => ({ path: [], children, mentions: [] }))
    p.then((d) => { setDetail(d); setErr('') }).catch((e) => setErr(e.message))
  }, [code])

  // 입력을 멈추면 검색 (코드 숫자는 즉시 결과가 바뀌어도 부담 없음)
  useEffect(() => {
    const t = q.trim()
    if (!t) return
    const id = setTimeout(() => {
      api<Found>(`/api/hs/search?q=${encodeURIComponent(t)}`).then(setFound).catch((e) => setErr(e.message))
    }, 300)
    return () => clearTimeout(id)
  }, [q])

  const pick = (c: string) => {
    setQ('')
    setCode(c)
  }
  const copy = (c: string) => {
    navigator.clipboard?.writeText(c).then(() => setCopied(c))
  }
  const cur = detail?.path.at(-1)
  const shown = q.trim() ? found : null // 검색어를 지우면 바로 탐색 화면으로

  return (
    <div className="page">
      <h2>HS 코드 검색</h2>
      <input
        className="search"
        value={q}
        autoFocus
        placeholder="코드(8481, 8481.80) 또는 품명(볼밸브, valve)으로 검색"
        onChange={(e) => setQ(e.target.value)}
      />
      {err && <div className="err">{err}</div>}

      {shown ? (
        <>
          {shown.headings.length > 0 && (
            <div className="meta">
              뜻이 가까운 호:{' '}
              {shown.headings.map((h) => (
                <button key={h.code} className="chip click" onClick={() => pick(h.code)}>
                  {h.code} {h.name_ko || h.name_en}
                </button>
              ))}
            </div>
          )}
          <p className="meta">품명·코드 일치 {shown.codes.length}{shown.codes.length === 300 ? '+ (검색어를 더 구체적으로)' : ''}건</p>
          <Rows rows={shown.codes} onPick={pick} context />
        </>
      ) : (
        <>
          <div className="crumbs">
            <button className="link" onClick={() => setCode(null)}>전체</button>
            {detail?.path.map((r) => (
              <span key={r.code}>
                {' › '}
                <button className="link" onClick={() => setCode(r.code)}>{fmt(r.code)}</button>
              </span>
            ))}
          </div>
          {cur && (
            <div className="form" style={{ margin: '12px 0' }}>
              <div>
                <b style={{ fontFamily: 'monospace', fontSize: '1.2em' }}>{fmt(cur.code)}</b>{' '}
                <span className="meta">{LEVEL[cur.code.length] ?? (cur.has_children ? '' : '최종 코드')}</span>{' '}
                <button className="link" onClick={() => copy(cur.code)}>{copied === cur.code ? '복사됨' : '코드 복사'}</button>
              </div>
              <div><Name r={cur} /></div>
            </div>
          )}
          {detail && <Rows rows={detail.children} onPick={setCode} />}
          {detail && detail.mentions.length > 0 && (
            <>
              <h3>이 호({cur!.code.slice(0, 4)})를 언급하는 규정 조항</h3>
              {detail.mentions.map((m, i) => (
                <div key={i} className="form" style={{ margin: '8px 0', gap: 4 }}>
                  <b>{m.code} {m.article ?? ''} {m.page ? `p.${m.page}` : ''}</b>
                  <span className="meta" style={{ whiteSpace: 'pre-wrap', marginTop: 0 }}>…{m.snippet}…</span>
                </div>
              ))}
            </>
          )}
          {detail && !code && detail.children.length === 0 && (
            <p className="meta">아직 HS 코드가 없습니다. 규정 업데이트에서 관세청 HS부호 엑셀과 Comtrade JSON을 올려 주세요.</p>
          )}
        </>
      )}
    </div>
  )
}
