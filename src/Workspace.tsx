import Chat from './Chat'
import HsSearch from './HsSearch'
import { Glossary, Regulations } from './Admin'

const MENU = [
  ['chat', '질문하기'],
  ['search', '검색'], // HS 전용
  ['regs', '규정 업데이트'],
  ['gloss', '용어 사전'],
] as const
const TITLE: Record<string, string> = { ALL: '통합 검토' }
export type Tab = (typeof MENU)[number][0]
export const TABS = MENU.map(([k]) => k)

type Props = { family: string; tab: Tab; onTab: (t: Tab) => void; onHome: () => void }

export default function Workspace({ family, tab, onTab, onHome }: Props) {
  // 통합 검토(ALL)는 질문만. 규정·용어는 각 규정 카드에서 관리
  const menu = MENU.filter(([k]) => (family === 'ALL' ? k === 'chat' : k !== 'search' || family === 'HS'))
  return (
    <div className="ws">
      <nav className="side">
        <h2>{TITLE[family] ?? family}</h2>
        {menu.map(([k, label]) => (
          <button key={k} className={tab === k ? 'cur' : ''} onClick={() => onTab(k)}>
            {label}
          </button>
        ))}
        <button className="back" onClick={onHome}>← 규정 선택</button>
      </nav>
      <div className="main">
        {/* 탭을 옮겨도 대화·검색 상태가 유지되도록 숨김 처리만 함 */}
        <div style={{ display: tab === 'chat' ? 'contents' : 'none' }}><Chat family={family} /></div>
        {family === 'HS' && <div style={{ display: tab === 'search' ? 'contents' : 'none' }}><HsSearch /></div>}
        {tab === 'regs' && <Regulations family={family} />}
        {tab === 'gloss' && <Glossary family={family} />}
      </div>
    </div>
  )
}
