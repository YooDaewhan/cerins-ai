import { useEffect, useRef, useState } from 'react'
import { api, jsonInit } from './api'
import Md from './Md'

type Source = { code: string; article: string | null; page: number | null }
type Msg = { role: 'me' | 'ai'; text: string; sources?: Source[]; error?: boolean }
type Ask = { answer: string; sources: Source[] }

// 도면만 첨부하고 질문을 비웠을 때 보낼 기본 질문, 빈 화면 예시
const DEFAULT_Q: Record<string, string> = {
  HS: '첨부한 도면의 품목에 맞는 HS 코드를 찾아 주세요.',
  ALL: '첨부한 도면 품목의 HS 코드와, 등록된 규정 각각에 해당하는지 검토해 주세요.',
}
const EXAMPLE: Record<string, string> = {
  HS: '예: "스테인리스 수동 볼밸브 HS코드가 뭔가요?"',
  ALL: '📎 도면 PDF를 올리고 전송하면 HS 코드와 적용 규정을 한번에 검토합니다',
}

// 조항 라벨이 여러 줄("Appendix No. 2 \nto the Technical ...")로 저장된 경우가 있어 첫 줄만, 중복 제거
const sourceLabels = (ss?: Source[]) =>
  [...new Set((ss ?? []).map((s) =>
    [s.code, (s.article ?? '').split('\n')[0].trim(), s.page ? `p.${s.page}` : ''].filter(Boolean).join(' · ')))]

// 글자 크기 (보는 사람 브라우저에만 저장)
const SIZES = [14, 16, 18, 20]
function loadSize() {
  try { return Number(localStorage.getItem('chat-font')) || 16 } catch { return 16 }
}

export default function Chat({ family }: { family: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [font, setFont] = useState(loadSize)
  const zoom = (d: number) => {
    const next = SIZES[Math.min(SIZES.length - 1, Math.max(0, SIZES.indexOf(font) + d))] ?? 16
    setFont(next)
    try { localStorage.setItem('chat-font', String(next)) } catch { /* 저장 불가(사생활 보호 모드 등) */ }
  }
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawing, setDrawing] = useState<{ name: string; text: string } | null>(null) // 첨부한 도면 사양
  const end = useRef<HTMLDivElement>(null)

  const [pending, setPending] = useState<File | null>(null) // 선택만 해 둔 도면 (전송 시 분석)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [msgs])

  async function send() {
    // 도면만 선택하고 질문을 비워 두면 기본 질문으로 검토
    const question = q.trim() || (pending
      ? DEFAULT_Q[family] ?? '첨부한 도면이 이 규정의 적용 대상인지 검토해 주세요.'
      : '')
    if (!question || busy) return
    const file = pending
    setQ('')
    setPending(null)
    setBusy(true)
    setMsgs((m) => [...m, { role: 'me', text: file ? `📎 ${file.name}\n${question}` : question }])
    try {
      let spec = drawing
      if (file) { // 선택한 도면을 먼저 분석하고, 읽은 사양을 보여준 뒤 같은 전송으로 질문에 답함
        const fd = new FormData()
        fd.append('file', file)
        const d = await api<{ text: string }>('/api/drawings', { method: 'POST', body: fd })
        spec = { name: file.name, text: d.text }
        setDrawing(spec)
        setMsgs((m) => [...m, { role: 'ai', text: `도면에서 읽은 사양입니다. 틀린 값이 있으면 채팅으로 알려주세요.\n\n${d.text}` }])
      }
      // 오류 메시지를 뺀 최근 대화를 함께 보내 후속 질문이 앞 조건을 이어가게 함
      const history = msgs.filter((m) => !m.error).slice(-6).map((m) => ({ role: m.role, text: m.text }))
      const r = await api<Ask>('/api/ask', jsonInit('POST', { question, family, history, drawing: spec?.text ?? null }))
      setMsgs((m) => [...m, { role: 'ai', text: r.answer, sources: r.sources }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'ai', text: `오류: ${(e as Error).message}`, error: true }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="msgs" style={{ fontSize: font }}>
        {msgs.length === 0 && (
          <div className="empty">
            <h2>{family === 'ALL' ? '통합 검토' : `${family} 질문하기`}</h2>
            <p>{EXAMPLE[family] ?? '예: "규격 15cm, 압력 14인데 적용 대상인가요?"'}</p>
          </div>
        )}
        {msgs.map((m, i) => {
          const src = sourceLabels(m.sources)
          return (
            <div key={i} className={m.role}>
              <div className={`bubble ${m.error ? 'err' : ''}`}>
                {m.role === 'ai' && !m.error ? <Md text={m.text} /> : m.text}
              </div>
              {src.length > 0 && (
                <details className="meta">
                  <summary>근거 {src.length}건</summary>
                  {src.map((s) => <span className="chip" key={s}>{s}</span>)}
                </details>
              )}
            </div>
          )
        })}
        {busy && (
          <div className="ai">
            <div className="bubble">검토 중…</div>
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="composer">
        {(pending || drawing) && (
          <div className="meta" style={{ maxWidth: 780, margin: '0 auto 6px' }}>
            {pending ? (
              <>전송할 도면: <span className="chip">{pending.name}</span>
                <button className="link" onClick={() => setPending(null)}>취소</button></>
            ) : (
              <>적용 중인 도면: <span className="chip">{drawing!.name}</span>
                <button className="link" onClick={() => setDrawing(null)}>해제</button></>
            )}
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          <div className="zoom">
            <button type="button" onClick={() => zoom(1)} disabled={font === SIZES.at(-1)} title="답변 글자 크게">가+</button>
            <button type="button" onClick={() => zoom(-1)} disabled={font === SIZES[0]} title="답변 글자 작게">가−</button>
          </div>
          <label className="btn" style={{ background: 'var(--panel)', color: 'inherit', border: '1px solid var(--border)', alignSelf: 'stretch', display: 'flex', alignItems: 'center', cursor: busy ? 'default' : 'pointer' }}>
            📎 도면
            <input type="file" accept=".pdf" hidden disabled={busy}
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) setPending(f) }} />
          </label>
          <textarea
            rows={2}
            value={q}
            placeholder="질문을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />
          <button className="btn" disabled={busy || (!q.trim() && !pending)}>
            전송
          </button>
        </form>
      </div>
    </>
  )
}
