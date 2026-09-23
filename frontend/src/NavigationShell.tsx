import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { api } from './api'
import type { User } from './types'
import { dateLabel, errorText } from './ui'
import './navigation.css'

type IconName = 'menu' | 'close' | 'people' | 'skills' | 'upload' | 'logout' | 'profile'

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="m15 5-7 7 7 7" />,
    people: <><circle cx="9" cy="8" r="3" /><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5" /></>,
    skills: <><path d="M4 20V10h4v10M10 20V4h4v16M16 20v-7h4v7M3 20h18" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
    logout: <><path d="M9 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4M9 12h12m-4-4 4 4-4 4" /></>,
    profile: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
  }
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function savedOpen() {
  try { return localStorage.getItem('cq.sidebar.open') !== 'false' }
  catch { return true }
}

export default function NavigationShell({ user, onLogout, children }: { user: User; onLogout: () => void; children: ReactNode }) {
  const location = useLocation()
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 900px)').matches)
  const [open, setOpen] = useState(() => !window.matchMedia('(max-width: 900px)').matches && savedOpen())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sidebar = useRef<HTMLElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const home = user.role === 'hr' ? '/hr/employees' : '/employee'
  const pageTitle = location.pathname === '/hr/import' ? 'Импорт профилей и истории' : location.pathname === '/hr/competencies' ? 'HR компетенции' : user.role === 'hr' ? 'HR view · Сотрудники' : 'Личный кабинет'

  function changeOpen(value: boolean) {
    setOpen(value)
    if (!mobile) {
      try { localStorage.setItem('cq.sidebar.open', String(value)) } catch { /* Navigation still works without browser storage. */ }
    }
    if (!value) toggle.current?.focus()
  }

  useEffect(() => {
    const media = window.matchMedia('(max-width: 900px)')
    const onChange = () => { setMobile(media.matches); setOpen(!media.matches && savedOpen()) }
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    if (!mobile || !open) return
    const oldOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    sidebar.current?.querySelector<HTMLButtonElement>('button')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') { setOpen(false); toggle.current?.focus() }
      if (event.key !== 'Tab') return
      const focusable = sidebar.current?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled)')
      if (!focusable?.length) return
      const first = focusable[0], last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.body.style.overflow = oldOverflow; document.removeEventListener('keydown', onKeyDown) }
  }, [mobile, open])

  async function logout() {
    setBusy(true); setError('')
    try { await api.logout(); onLogout() }
    catch (err) { setError(errorText(err)); setBusy(false) }
  }

  const closeOnMobile = () => { if (mobile) changeOpen(false) }
  return <div className={`workspace-layout${open ? ' sidebar-is-open' : ''}`}>
    {mobile && open && <button className="navigation-backdrop" tabIndex={-1} aria-label="Закрыть навигацию" onClick={() => changeOpen(false)} />}
    {open && <aside ref={sidebar} id="main-sidebar" className="navigation-sidebar" role={mobile ? 'dialog' : undefined} aria-modal={mobile ? true : undefined} aria-label="Боковая панель">
      <div className="navigation-brand-row"><Link className="brand" to={home} onClick={closeOnMobile}><span className="brand-mark">CQ</span><span>Career Quest<small>Платформа развития</small></span></Link><button className="sidebar-close" aria-label="Свернуть боковую панель" onClick={() => changeOpen(false)}><Icon name="close" /></button></div>
      <span className="navigation-section-label">{user.role === 'hr' ? 'HR ПРОСТРАНСТВО' : 'МОЁ РАЗВИТИЕ'}</span>
      <nav className="side-navigation" aria-label="Основная навигация">
        {user.role === 'hr' ? <>
          <NavLink to="/hr/employees" onClick={closeOnMobile}><Icon name="people" /><span>HR view<small>Профили сотрудников</small></span></NavLink>
          <NavLink to="/hr/competencies" onClick={closeOnMobile}><Icon name="skills" /><span>HR компетенции<small>Разрывы и участие</small></span></NavLink>
          <NavLink to="/hr/import" onClick={closeOnMobile}><Icon name="upload" /><span>Импорт профилей<small>Профили JSON · история CSV</small></span></NavLink>
        </> : <NavLink to="/employee" onClick={closeOnMobile}><Icon name="profile" /><span>Личный кабинет<small>Навыки и следующие шаги</small></span></NavLink>}
      </nav>
      <div className="navigation-footer"><div className="navigation-account"><span className="account-avatar">{user.role === 'hr' ? 'HR' : 'CQ'}</span><span><strong>{user.role === 'hr' ? 'HR-аккаунт' : 'Сотрудник'}</strong><small>{user.employee_id || 'Управление развитием'}</small></span></div>
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="button sidebar-logout" disabled={busy} onClick={() => void logout()}><Icon name="logout" />{busy ? 'Выходим…' : 'Выйти'}</button>
      </div>
    </aside>}
    <div className="workspace-main"><header className="workspace-header"><div className="workspace-topbar"><button ref={toggle} className="sidebar-toggle" aria-controls="main-sidebar" aria-expanded={open} aria-label={open ? 'Свернуть боковую панель' : 'Открыть боковую панель'} onClick={() => changeOpen(!open)}><Icon name="menu" /></button><span className="workspace-page-label">{pageTitle}</span><div className="workspace-status"><span>Срез: {dateLabel(user.as_of_date)}</span>{user.demo_mode && <span className="demo-chip">Демо</span>}</div></div></header>
      <div className="app-shell">{children}</div>
    </div>
  </div>
}
