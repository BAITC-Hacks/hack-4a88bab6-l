import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, Navigate, Route, Routes, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from './api'
import HrCompetencies from './HrCompetencies'
import ProfileView from './ProfileView'
import type { EmployeeList, User } from './types'
import { dateLabel, errorText } from './ui'

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      await api.login(email.trim(), password)
      const user = await api.me()
      onLogin(user)
      navigate(user.role === 'hr' ? '/hr/employees' : '/employee', { replace: true })
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  return <div className="login-page"><div className="login-decoration"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="login-copy"><span className="brand-kicker">CAREER QUEST</span><h1>Следующий шаг в развитии начинается здесь.</h1><p>Ваши навыки, цель и реальные возможности обучения — в одном месте.</p></div></div>
    <div className="login-form-side"><form className="login-card panel" onSubmit={event => void submit(event)}>
      <div className="login-icon" aria-hidden="true">CQ</div><span className="eyebrow">ДОБРО ПОЖАЛОВАТЬ</span><h2>Вход в Career Quest</h2><p>Войдите с демонстрационным аккаунтом сотрудника или HR.</p>
      <label>Электронная почта<input autoFocus type="email" autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} placeholder="name@careerquest.test" /></label>
      <label>Пароль<input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} placeholder="Введите пароль" /></label>
      {error && <div className="alert error" role="alert">{error}</div>}
      <button className="button primary login-submit" disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button>
      <small className="login-note">Демо-аккаунты и порядок настройки пароля указаны в README проекта.</small>
    </form></div>
  </div>
}

function Header({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function logout() {
    setBusy(true); setError('')
    try { await api.logout(); onLogout() }
    catch (err) { setError(errorText(err)); setBusy(false) }
  }
  return <header className="app-header"><div className="header-inner"><Link className="brand" to={user.role === 'hr' ? '/hr/employees' : '/employee'}><span className="brand-mark">CQ</span><span>Career Quest<small>Платформа развития</small></span></Link>
    <nav aria-label="Основная навигация">{user.role === 'hr' ? <><Link to="/hr/employees">Сотрудники</Link><Link to="/hr/competencies">Компетенции</Link></> : <Link to="/employee">Личный кабинет</Link>}</nav>
    <div className="header-actions"><span className="as-of">Срез: {dateLabel(user.as_of_date)}{user.demo_mode ? ' · демо' : ''}</span><button className="button subtle" disabled={busy} onClick={() => void logout()}>Выйти</button></div></div>{error && <div className="header-error" role="alert">{error}</div>}</header>
}

function HrEmployees() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selected = searchParams.get('id') || ''
  const [query, setQuery] = useState('')
  const [department, setDepartment] = useState('')
  const [role, setRole] = useState('')
  const [grade, setGrade] = useState('')
  const [list, setList] = useState<EmployeeList | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)

  const params = useMemo(() => {
    const value = new URLSearchParams()
    if (query.trim()) value.set('q', query.trim())
    if (department) value.set('department', department)
    if (role) value.set('role', role)
    if (grade) value.set('grade', grade)
    return value
  }, [query, department, role, grade])

  useEffect(() => {
    let current = true
    const timer = window.setTimeout(async () => {
      setLoading(true); setError('')
      try { const result = await api.employees(params); if (current) setList(result) }
      catch (err) { if (current) setError(errorText(err)) }
      finally { if (current) setLoading(false) }
    }, query ? 220 : 0)
    return () => { current = false; window.clearTimeout(timer) }
  }, [params, revision])

  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === 'visible') setRevision(value => value + 1) }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  const currentId = selected || list?.items[0]?.employee_id || ''
  return <div className="employees-page"><section className="page-title"><div><span className="eyebrow">HR · СОТРУДНИКИ</span><h1>Профили сотрудников</h1><p>Навыки, цель, рекомендации и история выбранного сотрудника.</p></div></section>
    <div className="employees-layout"><aside className="panel employee-sidebar"><div className="sidebar-title"><h2>Выбрать сотрудника</h2><span>{list?.items.length ?? 0}</span></div>
      <label className="search-box"><span>Поиск по имени или ID</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Например, E0005" /></label>
      <div className="sidebar-filters"><label>Отдел<select value={department} onChange={event => setDepartment(event.target.value)}><option value="">Все</option>{list?.departments.map(item => <option key={item}>{item}</option>)}</select></label><label>Роль<select value={role} onChange={event => setRole(event.target.value)}><option value="">Все</option>{list?.roles.map(item => <option key={item}>{item}</option>)}</select></label><label>Грейд<select value={grade} onChange={event => setGrade(event.target.value)}><option value="">Все</option>{list?.grades.map(item => <option key={item}>{item}</option>)}</select></label></div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="employee-list">{loading && !list ? <p className="empty">Загружаем список…</p> : list?.items.map(item => <button className={currentId === item.employee_id ? 'employee-option selected' : 'employee-option'} key={item.employee_id} onClick={() => setSearchParams({ id: item.employee_id })}><span className="avatar">{item.full_name.split(' ').map(part => part[0]).slice(0, 2).join('')}</span><span><b>{item.full_name}</b><small>{item.employee_id} · {item.role} · {item.grade}</small></span></button>)}{!loading && !list?.items.length && <p className="empty">Сотрудников по фильтру нет.</p>}</div>
    </aside><main className="employee-main">{currentId ? <ProfileView key={currentId} mode="hr" employeeId={currentId} /> : <div className="panel empty">Выберите сотрудника из списка.</div>}</main></div>
  </div>
}

export default function App() {
  const navigate = useNavigate()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshAuth = useCallback(async () => {
    try { setUser(await api.me()) }
    catch { setUser(null) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { void refreshAuth() }, [refreshAuth])
  useEffect(() => {
    const onUnauthorized = () => { setUser(null); navigate('/login', { replace: true }) }
    window.addEventListener('cq:unauthorized', onUnauthorized)
    return () => window.removeEventListener('cq:unauthorized', onUnauthorized)
  }, [navigate])

  if (loading) return <div className="app-loading"><div className="brand-mark">CQ</div><p>Загружаем Career Quest…</p></div>
  if (!user) return <Routes><Route path="/login" element={<Login onLogin={setUser} />} /><Route path="*" element={<Navigate to="/login" replace />} /></Routes>

  return <><Header user={user} onLogout={() => { setUser(null); navigate('/login', { replace: true }) }} /><div className="app-shell"><Routes>
    <Route path="/login" element={<Navigate to={user.role === 'hr' ? '/hr/employees' : '/employee'} replace />} />
    <Route path="/employee" element={user.role === 'employee' ? <ProfileView mode="employee" /> : <Navigate to="/hr/employees" replace />} />
    <Route path="/hr/employees" element={user.role === 'hr' ? <HrEmployees /> : <Navigate to="/employee" replace />} />
    <Route path="/hr/competencies" element={user.role === 'hr' ? <HrCompetencies /> : <Navigate to="/employee" replace />} />
    <Route path="*" element={<Navigate to={user.role === 'hr' ? '/hr/employees' : '/employee'} replace />} />
  </Routes></div></>
}
