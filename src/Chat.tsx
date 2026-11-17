import { useEffect, useRef, useState } from 'react'
import { api, jsonInit } from './api'
import Md from './Md'

type Source = { code: string; article: string | null; page: number | null }
// retry: 모델이 혼잡해 실패한 질문 (다른 모델로 다시 보낼 때 씀)
type Retry = { question: string; file: File | null }
type Msg = { role: 'me' | 'ai'; text: string; sources?: Source[]; error?: boolean; model?: string; retry?: Retry }
type Ask = { answer: string; sources: Source[]; model: string }
type Models = { models: string[]; answer: string }

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

// 글자 크기·모델 선택은 보는 사람 브라우저에만 저장
const SIZES = [14, 16, 18, 20]
const saved = (k: string) => { try { return localStorage.getItem(k) ?? '' } catch { return '' } }
const save = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* 저장 불가(사생활 보호 모드 등) */ } }

export default function Chat({ family }: { family: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [font, setFont] = useState(() => Number(saved('chat-font')) || 16)
  const zoom = (d: number) => {
    const next = SIZES[Math.min(SIZES.length - 1, Math.max(0, SIZES.indexOf(font) + d))] ?? 16
    setFont(next)
    save('chat-font', String(next))
  }
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawing, setDrawing] = useState<{ name: string; text: string } | null>(null) // 첨부한 도면 사양
  const [pending, setPending] = useState<File | null>(null) // 선택만 해 둔 도면 (전송 시 분석)
  const end = useRef<HTMLDivElement>(null)

  // 답변 모델: 서버가 알려준 사용 가능 목록 중 선택. 고른 적 없거나 목록에 없으면 서버 기본값
  const [list, setList] = useState<Models | null>(null)
  const [chosen, setChosen] = useState(() => saved('chat-model'))
  const model = list && list.models.includes(chosen) ? chosen : list?.answer ?? ''
  const choose = (m: string) => {
    setChosen(m)
    save('chat-model', m)
  }
  useEffect(() => {
    api<Models>('/api/models').then(setList).catch(() => setList(null))
  }, [])

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [msgs])

  // retry가 있으면 혼잡으로 실패한 질문을 고른 모델로 다시 보냄 (내 메시지는 다시 쓰지 않음)
  async function send(retry?: Retry, use = model) {
    // 도면만 선택하고 질문을 비워 두면 기본 질문으로 검토
    const question = retry?.question ?? (q.trim() || (pending
      ? DEFAULT_Q[family] ?? '첨부한 도면이 이 규정의 적용 대상인지 검토해 주세요.'
      : ''))
    if (!question || busy) return
    const file = retry ? retry.file : pending
    if (!retry) {
      setQ('')
      setPending(null)
      setMsgs((m) => [...m, { role: 'me', text: file ? `📎 ${file.name}\n${question}` : question }])
    }
    setBusy(true)
    let analyzed = false // 도면 분석까지 끝났으면 다시 보낼 때 도면은 빼고 질문만
    try {
      let spec = drawing
      if (file) { // 선택한 도면을 먼저 분석하고, 읽은 사양을 보여준 뒤 같은 전송으로 질문에 답함
        const fd = new FormData()
        fd.append('file', file)
        if (use) fd.append('model', use)
        const d = await api<{ text: string; model: string }>('/api/drawings', { method: 'POST', body: fd })
        spec = { name: file.name, text: d.text }
        setDrawing(spec)
        analyzed = true
        setMsgs((m) => [...m, { role: 'ai', text: `도면에서 읽은 사양입니다. 틀린 값이 있으면 채팅으로 알려주세요.\n\n${d.text}`, model: d.model }])
      }
      // 오류 메시지를 뺀 최근 대화를 함께 보내 후속 질문이 앞 조건을 이어가게 함
      const history = msgs.filter((m) => !m.error).slice(-6).map((m) => ({ role: m.role, text: m.text }))
      const r = await api<Ask>('/api/ask', jsonInit('POST', { question, family, history, drawing: spec?.text ?? null, model: use || null }))
      setMsgs((m) => [...m, { role: 'ai', text: r.answer, sources: r.sources, model: r.model }])
    } catch (e) {
      // 503 = 모델 혼잡 → 다른 모델로 다시 보낼지 물음
      const crowded = (e as { status?: number }).status === 503 && !!list
      if (crowded) api<Models>('/api/models').then(setList).catch(() => {}) // 못 쓰는 모델이 빠진 목록으로 갱신
      setMsgs((m) => [...m, {
        role: 'ai', text: `오류: ${(e as Error).message}`, error: true, model: use,
        retry: crowded ? { question, file: analyzed ? null : file } : undefined,
      }])
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
          const last = i === msgs.length - 1
          return (
            <div key={i} className={m.role}>
              <div className={`bubble ${m.error ? 'err' : ''}`}>
                {m.role === 'ai' && !m.error ? <Md text={m.text} /> : m.text}
              </div>
              {m.retry && last && list && (
                <div className="retry">
                  다른 모델로 다시 보낼까요?{' '}
                  {/* 혼잡할 때도 대체로 응답하는 lite 모델을 먼저 */}
                  {[...list.models].sort((a, b) => Number(b.includes('lite')) - Number(a.includes('lite')))
                    .filter((x) => x !== m.model).slice(0, 4).map((x) => (
                    <button key={x} className="chip click" disabled={busy}
                      onClick={() => { choose(x); send(m.retry, x) }}>{x}</button>
                  ))}
                </div>
              )}
              {(m.model && !m.error || src.length > 0) && (
                <div className="meta">
                  {m.model && !m.error && <span className="model-tag">모델: {m.model}</span>}
                  {src.length > 0 && (
                    <details>
                      <summary>근거 {src.length}건</summary>
                      {src.map((s) => <span className="chip" key={s}>{s}</span>)}
                    </details>
                  )}
                </div>
              )}
            </div>
          )
        })}
        {busy && (
          <div className="ai">
            <div className="bubble">검토 중… {model && <span className="meta">({model})</span>}</div>
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="composer">
        <div className="meta composer-bar">
          {list && (
            <label>
              답변 모델{' '}
              <select value={model} disabled={busy} onChange={(e) => choose(e.target.value)}>
                {list.models.map((x) => <option key={x} value={x}>{x}{x === list.answer ? ' (기본)' : ''}</option>)}
              </select>
            </label>
          )}
          {pending ? (
            <span>전송할 도면: <span className="chip">{pending.name}</span>
              <button className="link" onClick={() => setPending(null)}>취소</button></span>
          ) : drawing && (
            <span>적용 중인 도면: <span className="chip">{drawing.name}</span>
              <button className="link" onClick={() => setDrawing(null)}>해제</button></span>
          )}
        </div>
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
