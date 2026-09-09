import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, type AppState } from './api';
import { segmentText, type ChatMessage, type Draft, type Example, type Segment } from '../shared/types';

type Page = 'conversation' | 'history' | 'memory' | 'settings';
const pageNames: Record<Page, string> = { conversation: '会话工作台', history: '历史与风格', memory: '关系记忆', settings: '连接与运行' };
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    chat: <><path d="M20 11.5a8 8 0 0 1-8 8H5l-3 2V11.5a9 9 0 0 1 18 0Z"/><path d="M7 9h8M7 13h5"/></>,
    history: <><path d="M3 11a9 9 0 1 1 3 7M3 4v7h7"/><path d="M12 7v5l3 2"/></>,
    heart: <path d="m12 20-8-7a5 5 0 0 1 8-7 5 5 0 0 1 8 7Z"/>,
    settings: <><path d="M5 3v18M19 3v18M12 3v18"/><circle cx="5" cy="8" r="2.5" fill="currentColor"/><circle cx="12" cy="15" r="2.5" fill="currentColor"/><circle cx="19" cy="9" r="2.5" fill="currentColor"/></>,
    spark: <><path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7Z"/></>,
    arrow: <><path d="M4 12h16m-6-6 6 6-6 6"/></>,
    pause: <><path d="M8 5v14M16 5v14"/></>,
    play: <path d="m8 4 12 8-12 8Z"/>,
    check: <path d="m4 12 5 5L20 6"/>,
    refresh: <><path d="M20 7a9 9 0 0 0-15 0M4 17a9 9 0 0 0 15 0M20 2v5h-5M4 22v-5h5"/></>,
    link: <><path d="m10 13 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 0-6-6L7 5" transform="translate(2 2)"/></>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.chat}</svg>;
}
const dateTime = (n?: number | null) => n ? new Date(n).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
const fullDate = (n?: number | null) => n ? new Date(n).toLocaleDateString('zh-CN') : '—';
function Empty({ title, children, icon = 'chat' }: { title: string; children: ReactNode; icon?: string }) {
  return <div className="empty"><span className="empty-icon"><Icon name={icon} size={32}/></span><h3>{title}</h3><div>{children}</div></div>;
}
function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={'badge ' + tone}>{children}</span>; }
function SourceEvidence({ ids }: { ids: string[] }) {
  const [rows, setRows] = useState<Array<ChatMessage | { id: string; missing: true }> | null>(null);
  return <details className="source-evidence" onToggle={e => {
    if (e.currentTarget.open && !rows) void Promise.all(ids.map(id => api<ChatMessage>('/messages/' + encodeURIComponent(id)).catch(() => ({ id, missing: true as const })))).then(setRows);
  }}><summary>查看来源原文（{ids.length} 条）</summary>
    {!ids.length ? <p className="subtle-note">这条内容由你手工补充，没有历史引用。</p> : !rows ? <p className="subtle-note">正在读取来源…</p> : rows.map(m => <div className="source-row" key={m.id}>{'missing' in m ? <><small>来源暂不可用</small><div className="source-ids">{m.id}</div></> : <><small>{m.author === 'contact' ? '对方' : '本人'} · {dateTime(m.time)}</small><p>{m.text}</p></>}</div>)}
  </details>;
}
function textSegments(text: string): Segment[] {
  const segments: Segment[] = []; let offset = 0;
  for (const match of text.matchAll(/\[QQ表情:(\d+)\]/g)) {
    if (match.index! > offset) segments.push({ type: 'text', data: { text: text.slice(offset, match.index) } });
    segments.push({ type: 'face', data: { id: match[1] } }); offset = match.index! + match[0].length;
  }
  if (offset < text.length) segments.push({ type: 'text', data: { text: text.slice(offset) } });
  return segments;
}
export default function App() {
  const [state, setState] = useState<AppState | null>(null), [page, setPage] = useState<Page>('conversation');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [working, setWorking] = useState(false);
  const [serviceConnected, setServiceConnected] = useState<boolean | null>(null);
  const refresh = useCallback(async () => {
    try { setState(await api<AppState>('/state')); setServiceConnected(true); }
    catch { setServiceConnected(false); }
  }, []);
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 1500); return () => clearInterval(timer); }, [refresh]);
  async function act(fn: () => Promise<unknown>, success = '') {
    if (!serviceConnected) { setError('本地服务未连接，请先运行“启动QQ连接”。'); return; }
    setWorking(true); setError(''); setNotice('');
    try { await fn(); if (success) setNotice(success); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setWorking(false); }
  }
  const ready = serviceConnected && (state?.connection.ready ?? false), paused = state?.config.paused ?? false;
  const pending = state?.drafts.filter(d => ['pending', 'needs_human', 'unknown'].includes(d.status)).length ?? 0;
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><span/><span/><span/></div><div><strong>心声</strong><small>QQ 回复助手</small></div></div>
      <div className="nav-label">你的对话空间</div>
      <nav>{(['conversation', 'history', 'memory', 'settings'] as Page[]).map((p, i) =>
        <button key={p} className={'nav-item ' + (p === page ? 'active' : '')} onClick={() => setPage(p)} aria-current={p === page ? 'page' : undefined}>
          <Icon name={['chat', 'history', 'heart', 'settings'][i]}/><span>{pageNames[p]}</span>{p === 'conversation' && pending > 0 && <b>{pending}</b>}
        </button>)}</nav>
      <div className="sidebar-note"><Icon name="spark"/><p>熟悉你的表达，<br/>把决定留给你。</p></div>
      <div className="sidebar-bottom"><span className={'status-dot ' + (ready ? 'online' : '')}/><span>{!serviceConnected ? '本地服务未连接' : ready ? '后台服务正在运行' : '本地服务 · 等待 QQ'}</span><small>本机运行 · v0.1</small></div>
    </aside>
    <main>
      <header className="topbar"><div><div className="eyebrow">XINSHENG / PERSONAL CONVERSATIONS</div><h1>{pageNames[page]}</h1></div>
        <div className="top-actions"><Badge tone={!serviceConnected ? 'amber' : paused ? 'amber' : ready ? 'green' : 'neutral'}><span className={'status-dot ' + (ready && !paused ? 'online' : '')}/>{!serviceConnected ? '服务未连接' : paused ? '已暂停' : ready ? '连接就绪' : '等待连接'}</Badge>
          <button className="button secondary small" disabled={working || !ready} onClick={() => void act(() => api('/control/' + (paused ? 'resume' : 'pause'), 'POST'))}><Icon name={paused ? 'play' : 'pause'} size={16}/>{paused ? '恢复处理' : '暂停处理'}</button>
        </div>
      </header>
      <div className="page">
        {error && <div className="alert error" role="alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={16}/></button></div>}
        {notice && <div className="alert success" role="status"><Icon name="check" size={17}/><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice('')}><Icon name="close" size={16}/></button></div>}
        {serviceConnected && paused && <div className="alert warm"><span>{state?.config.pauseReason || '已暂停草稿生成。你仍可编辑并确认已有草稿。'}</span></div>}
        {!serviceConnected || !state ? <Empty title={serviceConnected === null ? '正在连接本地服务' : '本地服务已断开'} icon="link">请运行“启动QQ连接”，它会启动后台服务和 QQ 接入。<br/>记录与风格仍保存在本机，连接恢复后页面会自动更新。</Empty> : <>
          {page === 'conversation' && <Conversation state={state} act={act} working={working} goSettings={() => setPage('settings')}/>}
          {page === 'history' && <HistoryPage state={state} act={act} working={working}/>}
          {page === 'memory' && <MemoryPage state={state} act={act} working={working}/>}
          {page === 'settings' && <SettingsPage state={state} act={act} working={working}/>}
        </>}
      </div>
      <footer><span>心声只为选定的联系人处理新消息</span><span>生成内容由你掌握 · 发送结果以 QQ 回执为准</span></footer>
    </main>
  </div>;
}
type PageProps = { state: AppState; act: (fn: () => Promise<unknown>, success?: string) => Promise<void>; working: boolean };
function Conversation({ state, act, working, goSettings }: PageProps & { goSettings: () => void }) {
  const scroll = useRef<HTMLDivElement>(null), follow = useRef(true);
  const [editId, setEditId] = useState(''), [editText, setEditText] = useState('');
  const draft = state.drafts.find(d => ['pending', 'needs_human', 'unknown', 'sending'].includes(d.status));
  const currentText = draft && draft.id === editId ? editText : draft ? segmentText(draft.reply.segments) : '';
  useEffect(() => { if (scroll.current && follow.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [state.messages.at(-1)?.id]);
  const statusNames: Record<Draft['status'], string> = { pending: '待确认', needs_human: '需要你处理', sending: '正在发送', sent: '接口已确认发送', unknown: '发送结果待核', failed: '发送失败', stale: '已失效', discarded: '已放弃', skipped: '无需回复' };
  const save = async () => { if (draft && editId === draft.id) await api('/drafts/' + draft.id, 'PATCH', { segments: textSegments(editText) }); };
  return <>
    {!state.connection.ready && <div className="connect-banner"><div className="connect-symbol"><Icon name="link" size={24}/></div><div><strong>让熟悉的对话，接上你的表达</strong><p>先连接 QQ 并选择联系人。新的私聊和回复草稿会出现在这里。</p></div><button className="button primary" onClick={goSettings}>配置连接<Icon name="arrow" size={17}/></button></div>}
    <section className="conversation-toolbar"><div className="contact"><span className="avatar">{Array.from(state.config.contactName || '聊')[0]}</span><div><strong>{state.config.contactName || '尚未选择联系人'}</strong><small>{state.config.contactId ? 'QQ ' + state.config.contactId + ' · 单人私聊' : '选择后，仅处理这位联系人的消息'}</small></div></div>
      <div className="mode-switch" role="group" aria-label="回复模式">{(['confirm', 'auto'] as const).map(mode => <button key={mode} aria-pressed={state.config.mode === mode} className={state.config.mode === mode ? 'selected' : ''} disabled={working} onClick={() => void act(() => api('/config', 'PATCH', { mode }))}>{mode === 'confirm' ? '确认后发送' : '自动发送'}</button>)}</div>
    </section>
    <p className="conversation-help">{state.config.mode === 'confirm' ? '收到新消息 → 生成草稿 → 你审核、修改并确认发送。你在 QQ 手动回复后，下一轮仍会生成草稿。' : '新消息会自动生成并发送。你在 QQ 手动回复后，自动模式会暂停，直到你恢复处理。'}</p>
    <div className="conversation-grid">
      <section className="card chat-card"><div className="card-heading"><h2>最近对话</h2><span>{state.messageCount ? state.messageCount + ' 条已保存' : '等待第一条消息'}</span></div>
        <div className="chat-scroll" ref={scroll} onScroll={() => { const el = scroll.current!; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
          {!state.messages.length ? <Empty title="对话从这里开始">连接后，对方的新消息会自动出现。<br/>历史同步不会触发自动回复。</Empty> :
            state.messages.map(m => <article className={'message ' + (m.author === 'contact' ? 'received' : 'outgoing')} key={m.id}>
              <div className="message-meta">{m.author === 'contact' ? state.config.contactName || '对方' : m.author === 'assistant' ? '我 · 助手发送' : m.author === 'unknown' ? '我 · 来源待核' : '我'}<time>{dateTime(m.time)}</time>{m.origin === 'history' && <span>历史</span>}</div>
              <div className="bubble">{m.text}</div>
            </article>)}
        </div>
        <div className="chat-bottom"><span className={'status-dot ' + (state.connection.ready ? 'online' : '')}/>{state.worker.syncing ? '正在同步上下文…' : state.connection.ready ? '新消息会自动更新' : '等待 QQ 连接'}<span className="push-right">只处理文字与系统表情</span></div>
      </section>
      <section className="card draft-card"><div className="card-heading"><h2><Icon name="spark" size={18}/>回复草稿</h2>{draft ? <Badge tone={draft.status === 'unknown' ? 'amber' : 'green'}>{statusNames[draft.status]}</Badge> : <button className="button text-button small" disabled={working || !state.connection.ready || state.worker.sending} onClick={() => void act(() => api('/drafts/compose', 'POST'))}>手工回复</button>}</div>
        {state.worker.generating ? <div className="generating"><div className="pulse"><span/><span/><span/></div><strong>正在斟酌表达</strong><p>结合当前对话、你的风格与相关记忆。</p><small>收到新消息时，会重新整理这一轮回复。</small></div> : draft ? <>
          {draft.reply.reason && <div className="draft-reason">{draft.reply.reason}</div>}
          <label className="sr-only" htmlFor="reply-draft">回复内容</label><textarea id="reply-draft" className="draft-editor" placeholder="在这里写下你的回复…" value={currentText} disabled={!['pending', 'needs_human'].includes(draft.status)} onChange={e => { setEditId(draft.id); setEditText(e.target.value); }}/>
          <div className="editor-meta"><span>{currentText.length} 字</span><span>QQ 表情以 [QQ表情:编号] 保留</span></div>
          {draft.status === 'unknown' ? <div className="draft-actions column"><p>请在 QQ 中核对，系统不会自动重发。</p><button className="button secondary" onClick={() => void act(() => api('/drafts/' + draft.id + '/resolve', 'POST', { outcome: 'sent' }))}>我已核对：已发送</button><button className="button text-button" onClick={() => void act(() => api('/drafts/' + draft.id + '/resolve', 'POST', { outcome: 'not_sent' }))}>我已核对：未发送，放弃此条</button></div> :
            <div className="draft-actions"><button className="button primary full" disabled={working || !currentText.trim() || draft.status === 'sending'} onClick={() => void act(async () => { await save(); const sent = await api('/drafts/' + draft.id + '/send', 'POST'); if (sent.status !== 'sent') throw new Error(sent.error || '发送结果待核'); }, 'QQ 接口已确认发送。')}>确认发送<Icon name="arrow" size={17}/></button><div className="two-buttons"><button className="button secondary" disabled={working || state.config.paused} onClick={() => void act(() => api('/drafts/generate', 'POST'))}><Icon name="refresh" size={15}/>重新生成</button><button className="button text-button" disabled={working} onClick={() => void act(() => api('/drafts/' + draft.id + '/discard', 'POST'))}>放弃草稿</button></div></div>}
        </> : state.config.paused ? <Empty title="草稿生成已暂停" icon="pause">暂停期间仍会接收消息，暂不生成回复。<br/>点击右上角“恢复处理”后，下一条新消息会生成草稿；已有消息可在恢复后手动生成。</Empty> : state.worker.pendingCount > 0 ? <Empty title="正在合并连续消息" icon="spark">等待对方说完这一轮，再生成一条回复草稿。</Empty> : <Empty title="等待对方的新消息" icon="spark">新的文字或系统表情消息到达后会自动生成草稿。<br/>{state.config.mode === 'confirm' ? '你审核并点击“确认发送”后才会发出。' : '自动模式仅处理之后的新消息。'}<button className="button secondary" disabled={working || !state.connection.ready || state.messages.at(-1)?.author !== 'contact'} onClick={() => void act(() => api('/drafts/generate', 'POST'))}>为最新消息生成草稿</button></Empty>}
        <div className="draft-foot"><span>当前风格</span><strong>{state.profile?.name || '平实表达 · 尚未绑定画像'}</strong>{state.worker.lastLatencyMs > 0 && <small>最近生成 {(state.worker.lastLatencyMs / 1000).toFixed(1)} 秒</small>}</div>
      </section>
    </div>
    {state.drafts.length > 0 && <section className="card activity-card"><div className="card-heading"><h2>处理记录</h2><span>保留结果与需要你关注的地方</span></div>{state.drafts.slice(0, 8).map(d => <div className="activity-row" key={d.id}><span className={'activity-dot ' + (d.status === 'sent' ? 'good' : '')}/><span>{statusNames[d.status]}</span><p>{d.error || segmentText(d.reply.segments) || d.reply.reason}</p><time>{dateTime(d.updatedAt)}</time></div>)}</section>}
  </>;
}
function HistoryPage({ state, act, working }: PageProps) {
  const [range, setRange] = useState('90'), [start, setStart] = useState(() => new Date(Date.now() - 90 * 86400000).toLocaleDateString('en-CA')), [end, setEnd] = useState(() => new Date().toLocaleDateString('en-CA'));
  const [profileId, setProfileId] = useState(''), [style, setStyle] = useState(''), [name, setName] = useState(''), [showOriginal, setShowOriginal] = useState(false);
  const [examples, setExamples] = useState<Example[]>([]);
  const [review, setReview] = useState<Record<string, string>>({});
  const jobs = state.historyJobs;
  const status: Record<string, string> = { running: '进行中', completed: '已完成', paused: '已暂停', partial: '部分取得 · 待核', failed: '失败' };
  return <>
    <div className="section-intro"><h2>从真实对话里，找到你的表达习惯</h2><p>历史用于理解和参考，不会触发自动发送。事实与风格都可以由你修改。</p></div>
    <div className="two-column">
      <section className="card padded"><div className="title-line"><span className="step">01</span><h2>获取历史记录</h2></div><p className="muted">读取当前联系人的可用历史，保存分页进度。</p>
        <div className="choice-row"><label><input type="radio" name="range" checked={range === '90'} onChange={() => setRange('90')}/>最近 90 天</label><label><input type="radio" name="range" checked={range === 'custom'} onChange={() => setRange('custom')}/>自选范围</label><label><input type="radio" name="range" checked={range === 'all'} onChange={() => setRange('all')}/>全部可读</label></div>
        {range === 'custom' && <div className="form-row"><label>开始日期<input type="date" value={start} onChange={e => setStart(e.target.value)}/></label><label>结束日期<input type="date" value={end} onChange={e => setEnd(e.target.value)}/></label></div>}
        <button className="button primary" disabled={working || !state.connection.ready || jobs.some(j => j.status === 'running')} onClick={() => void act(() => api('/history', 'POST', { startTime: range === 'all' ? null : range === '90' ? Date.now() - 90 * 86400000 : new Date(start + 'T00:00:00').getTime(), endTime: range === 'custom' ? new Date(end + 'T23:59:59.999').getTime() : Date.now() }))}>开始导入<Icon name="arrow" size={16}/></button>
        <div className="subtle-note">显示的是接口实际返回的覆盖范围，不代表已取得全部漫游记录。</div>
        {jobs.slice(0, 4).map(j => <div className="job" key={j.id}><div><strong>{status[j.status]}</strong><span>{j.imported} 条新增 · {j.pages} 页</span></div><p>{fullDate(j.earliest)} — {fullDate(j.latest)}</p>{j.error && <small>{j.error}</small>}<div className="job-action"><button className="button text-button small" disabled={working || !state.connection.ready || j.status === 'completed'} onClick={() => void act(() => api('/history/' + j.id + (j.status === 'running' ? '/pause' : '/resume'), 'POST'))}>{j.status === 'running' ? '暂停导入' : '继续导入'}</button></div></div>)}
      </section>
      <section className="card padded"><div className="title-line"><span className="step">02</span><h2>整理风格与记忆</h2></div><p className="muted">逐批分析已保存的历史，只从本人真实发言中学习风格。</p>
        <div className="stat-line"><strong>{state.messageCount.toLocaleString()}</strong><span>条已保存消息</span><strong>{state.profiles.length}</strong><span>份风格画像</span></div>
        <button className="button primary" disabled={working || state.analysis.running || !state.messageCount} onClick={() => void act(() => api('/analysis', 'POST'))}>从历史提取风格<Icon name="spark" size={16}/></button>
        {state.analysis.running && <div className="progress-block"><strong>{state.analysis.task}</strong><progress max={state.analysis.total || 1} value={state.analysis.processed}/><div>{state.analysis.processed} / {state.analysis.total}<button className="button text-button small" onClick={() => void act(() => api('/analysis/pause', 'POST'))}>暂停任务</button></div></div>}
        {state.analysis.error && <p className="inline-error">{state.analysis.error}</p>}
        {state.analysisJobs.slice(0, 3).map(j => <div className="job" key={j.id}><div><strong>{status[j.status]}</strong><span>{j.processed} / {j.total} 条</span></div>{j.error && <small>{j.error}</small>}{['paused', 'failed'].includes(j.status) && <button className="button text-button small" disabled={working || state.analysis.running} onClick={() => void act(() => api('/analysis/' + j.id + '/resume', 'POST'))}>继续分析</button>}</div>)}
        <p className="subtle-note">分析完成后，请在下方选用画像；提取的关系事实初始标为“待核”。</p>
      </section>
    </div>
    <section className="card padded"><div className="card-title-actions"><h2>我的表达风格</h2><button className="button secondary small" disabled={working} onClick={() => void act(async () => { const p = await api('/profiles', 'POST', { name: '我的表达风格' }); setProfileId(p.id); setStyle(p.style); setName(p.name); setExamples(p.examples); })}>手工创建画像</button></div>
      <div className="form-row"><label className="grow">当前使用的画像<select value={state.config.profileId} disabled={working} onChange={e => void act(() => api('/config', 'PATCH', { profileId: e.target.value }))}><option value="">平实表达 · 暂不绑定</option>{state.profiles.map(p => <option value={p.id} key={p.id}>{p.name} · {p.exampleCount} 个示例</option>)}</select></label><button className="button secondary align-end" disabled={!state.config.profileId || working} onClick={() => void act(async () => { const p = await api('/profiles/' + state.config.profileId); setProfileId(p.id); setStyle(p.style); setName(p.name); setExamples(p.examples); })}>编辑画像</button></div>
      {profileId ? <div className="profile-editor"><label>画像名称<input value={name} onChange={e => setName(e.target.value)}/></label><label>风格说明<textarea rows={8} value={style} onChange={e => setStyle(e.target.value)} placeholder="描述你常用的称呼、语气、句子长短和表达方式…"/></label>
        <div className="card-title-actions"><h3>表达示例</h3><button className="button secondary small" onClick={() => setExamples([...examples, { id: crypto.randomUUID(), context: '', reply: '', sourceIds: [], sourceTime: Date.now(), edited: true }])}>添加示例</button></div><p className="subtle-note">可修改语境和回复。编辑后的示例会单独标明，原有来源仍可查看；点击保存画像后生效。</p>
        {examples.map((e, index) => <div className="example-editor" key={e.id}><div className="card-title-actions"><strong>示例 {index + 1}</strong><button className="button text-button small" onClick={() => setExamples(examples.filter(item => item.id !== e.id))}>移除示例 {index + 1}</button></div>
          <label>示例 {index + 1} 语境<textarea rows={2} value={e.context} onChange={event => setExamples(examples.map(item => item.id === e.id ? { ...item, context: event.target.value, sourceTime: Date.now(), edited: true } : item))}/></label>
          <label>示例 {index + 1} 回复<textarea rows={2} value={e.reply} onChange={event => setExamples(examples.map(item => item.id === e.id ? { ...item, reply: event.target.value, sourceTime: Date.now(), edited: true } : item))}/></label><SourceEvidence ids={e.sourceIds}/></div>)}
        <div className="button-row"><button className="button primary" disabled={working} onClick={() => void act(() => api('/profiles/' + profileId, 'PATCH', { name, style, examples }), '画像已保存。')}>保存画像</button><button className="button text-button" onClick={() => setProfileId('')}>结束编辑</button></div></div> : state.profile ? <div className="style-text">{state.profile.style || '尚未填写风格说明。'}</div> : <Empty title="还没有选用风格画像" icon="spark">导入并分析历史，或先手工描述你的表达习惯。</Empty>}
      {state.profile?.examples?.length ? <details className="examples"><summary>查看表达示例（最近 {state.profile.examples.length} 段）</summary>{state.profile.examples.map(e => <div className="example" key={e.id}><p><span>语境</span>{e.context}</p><p><span>{e.edited ? '手工示例' : '本人原话'}</span>{e.reply}</p><small>{e.edited ? '手工修改 · ' : ''}来源 {e.sourceIds.length} 条 · {dateTime(e.sourceTime)}</small><SourceEvidence ids={e.sourceIds}/></div>)}</details> : null}
    </section>
    <section className="card padded"><div className="card-title-actions"><div><h2>离线回放</h2><p className="muted">最多 20 段历史对话，隐藏原回复和后续信息；模型生成不会发送到 QQ。</p></div><button className="button secondary" disabled={working || state.analysis.running || !state.messageCount} onClick={() => void act(() => api('/replays', 'POST'))}>开始回放</button></div>
      {state.replays.length ? <><label className="check-line"><input type="checkbox" checked={showOriginal} onChange={e => setShowOriginal(e.target.checked)}/>显示本人原回复作对照</label>{state.replays.slice(0, 2).map(r => <div className="replay" key={r.id}><h3>{dateTime(r.createdAt)} · {status[r.status]} · {r.results.length}/{r.count} 段</h3><p className="muted">训练样本 {r.trainingCount} 条，均早于首个回放案例。{r.count < 20 && '可用案例不足 20 段。'}</p>{r.error && <p className="inline-error">{r.error}</p>}{r.results.map((item: any, index: number) => { const key = r.id + ':' + index; return <div className="replay-case" key={key}><div className="replay-context">{item.input.map((m: any, i: number) => <p key={i}><span>{m.author === 'contact' ? '对方' : '我'}</span>{m.text}</p>)}</div><p className="replay-answer"><strong>生成回复</strong>{segmentText(item.generated.segments) || item.generated.reason}</p>{showOriginal && <p className="replay-answer"><strong>本人原回复</strong>{item.expected}</p>}<div className="review-row"><input aria-label={'第 ' + (index + 1) + ' 段回放意见'} value={review[key] ?? item.review} onChange={e => setReview({ ...review, [key]: e.target.value })} placeholder="记下这段哪里像你、哪里需要调整…"/><button className="button secondary small" onClick={() => void act(() => api('/replays/' + r.id + '/' + index, 'PATCH', { review: review[key] ?? item.review }), '回放意见已保存。')}>保存意见</button></div></div>; })}</div>)}</> : <p className="subtle-note">回放结果将保留，表达是否贴近本人由你判断，不自动打分。</p>}
    </section>
  </>;
}
function MemoryPage({ state, act, working }: PageProps) {
  const [text, setText] = useState(''), [status, setStatus] = useState<'confirmed' | 'unverified'>('unverified'), [editing, setEditing] = useState('');
  return <>
    <div className="section-intro"><h2>记住真实的事，也留住不确定的边界</h2><p>关系事实带有来源与时间。自动提取的内容先进入待核，你可以确认、修改或移除。</p></div>
    <div className="memory-layout"><section className="memory-list">
      {!state.memories.length ? <section className="card"><Empty title="有依据的记忆，从这里积累" icon="heart">提取历史后会出现关系事实，<br/>也可以手工补充你希望助手记住的事。</Empty></section> : state.memories.map(m => <article className="card memory-card" key={m.id}><div><Badge tone={m.status === 'confirmed' ? 'green' : 'amber'}>{m.status === 'confirmed' ? '已确认' : '待核'}</Badge><time>{fullDate(m.sourceTime)}</time></div><p>{m.text}</p><small>{m.sourceIds.length ? '关联 ' + m.sourceIds.length + ' 条历史消息' : '由你手工补充或修改'}</small><div className="button-row">{m.status === 'unverified' && <button className="button text-button small" disabled={working} onClick={() => void act(() => api('/memories/' + m.id, 'PATCH', { status: 'confirmed' }))}>确认事实</button>}<button className="button text-button small" onClick={() => { setEditing(m.id); setText(m.text); setStatus(m.status); }}>编辑</button><button className="button text-button small danger-text" disabled={working} onClick={() => void act(() => api('/memories/' + m.id, 'DELETE'), '已移除这条记忆。')}>移除</button></div><SourceEvidence ids={m.sourceIds}/></article>)}
    </section><section className="card padded memory-form"><h2>{editing ? '编辑记忆' : '补充一条记忆'}</h2><label>事实内容<textarea rows={7} value={text} onChange={e => setText(e.target.value)} placeholder="例如：对方明确提到的偏好、你们已确认的约定…"/></label><label>核实状态<select value={status} onChange={e => setStatus(e.target.value as typeof status)}><option value="unverified">待核</option><option value="confirmed">已确认</option></select></label><button className="button primary full" disabled={working || !text.trim() || !state.config.contactId} onClick={() => void act(async () => { await api(editing ? '/memories/' + editing : '/memories', editing ? 'PATCH' : 'POST', { text, status }); setText(''); setEditing(''); }, '记忆已保存。')}>保存记忆</button>{editing && <button className="button text-button full" onClick={() => { setEditing(''); setText(''); }}>取消编辑</button>}<p className="subtle-note">对旧事实的修改按当前时间记录，避免把后来知道的事实带入历史回放。</p></section></div>
  </>;
}
function SettingsPage({ state, act, working }: PageProps) {
  const [form, setForm] = useState(() => ({ wsUrl: state.config.wsUrl, token: '', accountId: state.config.accountId, contactId: state.config.contactId, contactName: state.config.contactName, codexPath: state.config.codexPath, model: state.config.model, debounceMs: state.config.debounceMs, generationTimeoutMs: state.config.generationTimeoutMs, replyEffort: state.config.replyEffort, analysisEffort: state.config.analysisEffort }));
  const [friends, setFriends] = useState<any[]>([]), [probe, setProbe] = useState<any>(null), [probeBusy, setProbeBusy] = useState(false);
  const set = (key: keyof typeof form, value: string | number) => setForm(f => ({ ...f, [key]: value }));
  const save = async () => { const { token, ...rest } = form; await api('/config', 'PATCH', { ...rest, ...(token ? { token } : {}) }); setForm(f => ({ ...f, token: '' })); };
  return <>
    <div className="status-grid">
      {[['QQ 在线', state.connection.online ? '在线' : '未在线', state.connection.online], ['消息连接', state.connection.connected ? '已连接' : '未连接', state.connection.connected], ['消息处理器', state.worker.workerRunning ? '已启动' : '未启动', state.worker.workerRunning], ['待处理消息', String(state.worker.pendingCount), false]].map(([label, value, good]) => <div className="card status-card" key={String(label)}><span>{label}</span><strong className={good ? 'green-text' : ''}>{value}</strong></div>)}
    </div>
    <div className="two-column settings-columns"><section className="card padded"><div className="title-line"><span className="step">01</span><h2>QQ 连接与联系人</h2></div><p className="muted">先启动配套 NapCat，并在手机上自行完成 QQ 扫码登录。</p>
      <label>本机连接地址<input value={form.wsUrl} onChange={e => set('wsUrl', e.target.value)} placeholder="ws://127.0.0.1:3001"/></label>
      <label>连接令牌<input type="password" autoComplete="off" value={form.token} onChange={e => set('token', e.target.value)} placeholder={state.config.tokenPresent ? '已保存；留空保持原值' : '填写与 NapCat 相同的令牌'}/></label>
      <div className="button-row"><button className="button primary" disabled={working} onClick={() => void act(async () => { await save(); await api('/connect', 'POST'); })}>保存并连接</button><button className="button secondary" disabled={working || !state.connection.connected} onClick={() => void act(() => api('/disconnect', 'POST'))}>断开连接</button></div>
      {state.connection.accountId && <div className="detected-account"><span>当前连接的 QQ：<strong>{state.connection.accountId}</strong></span><button className="button text-button small" onClick={() => set('accountId', state.connection.accountId)}>填入下方账号</button></div>}
      <div className="form-row"><label>承担回复的 QQ<input inputMode="numeric" value={form.accountId} onChange={e => set('accountId', e.target.value)} placeholder="与你扫码登录的账号一致"/></label><label>目标联系人 QQ<input inputMode="numeric" value={form.contactId} onChange={e => set('contactId', e.target.value)} placeholder="仅为这个联系人回复"/></label></div>
      <label>联系人显示名称<input value={form.contactName} onChange={e => set('contactName', e.target.value)} placeholder="你熟悉的称呼"/></label>
      <div className="button-row"><button className="button secondary small" disabled={working || !state.connection.connected} onClick={() => void act(async () => setFriends(await api('/friends')))}>从好友列表选择</button>{friends.length > 0 && <select aria-label="选择目标好友" value={form.contactId} onChange={e => { const f = friends.find(f => String(f.user_id) === e.target.value); if (f) setForm(old => ({ ...old, contactId: String(f.user_id), contactName: f.remark || f.nickname || '' })); }}><option value="">选择好友</option>{friends.map(f => <option key={String(f.user_id)} value={String(f.user_id)}>{f.remark || f.nickname} · {f.user_id}</option>)}</select>}</div>
      <p className="subtle-note">保存联系人会同步最近上下文；历史不会自动补发。切换账号时，请核对目标联系人与画像。</p>
      <button className="button primary" disabled={working || !form.accountId || !form.contactId} onClick={() => void act(save, '账号与联系人已保存。')}>保存账号与联系人</button>
    </section>
    <section className="card padded"><div className="title-line"><span className="step">02</span><h2>本机 Codex</h2></div><p className="muted">复用本机 ChatGPT 登录。选定的聊天上下文会提交到云端模型。</p>
      <label>Codex 程序位置<input value={form.codexPath} onChange={e => set('codexPath', e.target.value)} placeholder="codex 或本机可执行文件路径"/></label><label>模型名称<input value={form.model} onChange={e => set('model', e.target.value)}/></label>
      <div className="form-row"><label>回复推理强度<select value={form.replyEffort} onChange={e => set('replyEffort', e.target.value)}><option value="low">低 · 默认</option><option value="medium">中</option><option value="high">高</option></select></label><label>风格整理强度<select value={form.analysisEffort} onChange={e => set('analysisEffort', e.target.value)}><option value="low">低</option><option value="medium">中 · 默认</option><option value="high">高</option></select></label></div>
      <div className="form-row"><label>连续消息合并等待（秒）<input type="number" min=".5" max="30" step=".5" value={form.debounceMs / 1000} onChange={e => set('debounceMs', Number(e.target.value) * 1000)}/></label><label>生成超时（秒）<input type="number" min="10" max="600" value={form.generationTimeoutMs / 1000} onChange={e => set('generationTimeoutMs', Number(e.target.value) * 1000)}/></label></div>
      <div className="button-row"><button className="button primary" disabled={working} onClick={() => void act(save, '模型设置已保存。')}>保存设置</button><button className="button secondary" disabled={working || probeBusy} onClick={() => void act(async () => { setProbeBusy(true); try { await save(); setProbe(await api('/codex/probe', 'POST')); } finally { setProbeBusy(false); } })}>{probeBusy ? '正在测试…' : '测试 Codex 连接'}</button></div>
      {probe && <div className="probe-result"><Icon name="check" size={18}/><div><strong>{probe.reply?.reason || '测试成功'}</strong><p>{probe.latencyMs ? (probe.latencyMs / 1000).toFixed(1) + ' 秒 · 输入 ' + (probe.usage?.input_tokens ?? '—') + ' tokens' : '测试环境'}</p></div></div>}
      <p className="subtle-note">连接测试不读取聊天、不发送 QQ 消息，会使用一次模型调用。</p>
    </section></div>
    <section className="card padded"><h2>运行记录</h2><div className="runtime-grid"><div><span>最近心跳</span><strong>{dateTime(state.connection.lastHeartbeat)}</strong></div><div><span>最近生成耗时</span><strong>{state.worker.lastLatencyMs ? (state.worker.lastLatencyMs / 1000).toFixed(1) + ' 秒' : '—'}</strong></div><div><span>最近输入 / 输出 tokens</span><strong>{state.worker.lastUsage.input_tokens ?? '—'} / {state.worker.lastUsage.output_tokens ?? '—'}</strong></div></div>{(state.connection.error || state.connection.lastError || state.worker.lastError) && <pre className="error-detail">{state.connection.error || state.connection.lastError || state.worker.lastError}</pre>}
      <div className="stop-row"><div><strong>关闭页面后，后台仍会继续运行</strong><p>需要完全停止时，使用右侧按钮或项目的停止入口。</p></div><button className="button secondary danger-text" disabled={working} onClick={() => void act(() => api('/control/stop', 'POST'), '已请求停止后台服务。')}>停止后台服务</button></div>
    </section>
  </>;
}
