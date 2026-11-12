import { useEffect, useRef, useState } from 'react'
import { api, jsonInit } from './api'

type Source = { code: string; article: string | null; page: number | null }
type Msg = { role: 'me' | 'ai'; text: string; sources?: Source[]; error?: boolean }
type Ask = { answer: string; sources: Source[] }

export default function Chat({ family }: { family: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawing, setDrawing] = useState<{ name: string; text: string } | null>(null) // 첨부한 도면 사양
  const end = useRef<HTMLDivElement>(null)

  async function attach(file: File) {
    if (busy) return
    setBusy(true)
    setMsgs((m) => [...m, { role: 'me', text: `📎 ${file.name}` }])
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await api<{ text: string }>('/api/drawings', { method: 'POST', body: fd })
      setDrawing({ name: file.name, text: r.text })
      setMsgs((m) => [...m, { role: 'ai', text: `도면에서 읽은 사양입니다. 틀린 값이 있으면 채팅으로 알려주세요.\n\n${r.text}` }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'ai', text: `도면 분석 오류: ${(e as Error).message}`, error: true }])
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [msgs])

  async function send() {
    const question = q.trim()
    if (!question || busy) return
    setQ('')
    setBusy(true)
    setMsgs((m) => [...m, { role: 'me', text: question }])
    try {
      // 오류 메시지를 뺀 최근 대화를 함께 보내 후속 질문이 앞 조건을 이어가게 함
      const history = msgs.filter((m) => !m.error).slice(-6).map((m) => ({ role: m.role, text: m.text }))
      const r = await api<Ask>('/api/ask', jsonInit('POST', { question, family, history, drawing: drawing?.text ?? null }))
      setMsgs((m) => [...m, { role: 'ai', text: r.answer, sources: r.sources }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'ai', text: `오류: ${(e as Error).message}`, error: true }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="msgs">
        {msgs.length === 0 && (
          <div className="empty">
            <h2>{family} 질문하기</h2>
            <p>예: "규격 15cm, 압력 14인데 적용 대상인가요?"</p>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={m.role}>
            <div className={`bubble ${m.error ? 'err' : ''}`}>{m.text}</div>
            {m.sources && m.sources.length > 0 && (
              <div className="meta">
                근거:{' '}
                {m.sources.map((s, j) => (
                  <span className="chip" key={j}>
                    {s.code} {s.article ?? ''} {s.page ? `p.${s.page}` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="ai">
            <div className="bubble">검토 중…</div>
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="composer">
        {drawing && (
          <div className="meta" style={{ maxWidth: 780, margin: '0 auto 6px' }}>
            적용 중인 도면: <span className="chip">{drawing.name}</span>
            <button className="link" onClick={() => setDrawing(null)}>해제</button>
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          <label className="btn" style={{ background: 'var(--panel)', color: 'inherit', border: '1px solid var(--border)', alignSelf: 'stretch', display: 'flex', alignItems: 'center', cursor: busy ? 'default' : 'pointer' }}>
            📎 도면
            <input type="file" accept=".pdf" hidden disabled={busy}
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) attach(f) }} />
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
          <button className="btn" disabled={busy || !q.trim()}>
            전송
          </button>
        </form>
      </div>
    </>
  )
}
