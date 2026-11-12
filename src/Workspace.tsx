import { useState } from 'react'
import Chat from './Chat'
import { Glossary, Regulations } from './Admin'

const MENU = [
  ['chat', '질문하기'],
  ['regs', '규정 업데이트'],
  ['gloss', '용어 사전'],
] as const
type Tab = (typeof MENU)[number][0]

export default function Workspace({ family, onHome }: { family: string; onHome: () => void }) {
  const [tab, setTab] = useState<Tab>('chat')
  return (
    <div className="ws">
      <nav className="side">
        <h2>{family}</h2>
        {MENU.map(([k, label]) => (
          <button key={k} className={tab === k ? 'cur' : ''} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
        <button className="back" onClick={onHome}>← 규정 선택</button>
      </nav>
      <div className="main">
        {/* 탭을 옮겨도 대화가 유지되도록 Chat은 숨김 처리만 함 */}
        <div style={{ display: tab === 'chat' ? 'contents' : 'none' }}><Chat family={family} /></div>
        {tab === 'regs' && <Regulations family={family} />}
        {tab === 'gloss' && <Glossary family={family} />}
      </div>
    </div>
  )
}
