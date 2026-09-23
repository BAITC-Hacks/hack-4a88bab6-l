import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, Navigate, Route, Routes, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from './api'
import HrCompetencies from './HrCompetencies'
import HrImport from './HrImport'
import NavigationShell from './NavigationShell'
import HrEmployeeReview from './HrEmployeeReview'
import ProfileView from './ProfileView'
import type { Competencies, EmployeeList, User } from './types'
import { availabilityLabels, errorText } from './ui'

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

function HrEmployees() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selected = searchParams.get('id') || ''
  const [query, setQuery] = useState('')
  const [department, setDepartment] = useState('')
  const [role, setRole] = useState('')
  const [grade, setGrade] = useState('')
  const [focus, setFocus] = useState('all')
  const [list, setList] = useState<EmployeeList | null>(null)
  const [overview, setOverview] = useState<Competencies | null>(null)
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
    let current = true
    api.competencies(new URLSearchParams()).then(result => { if (current) setOverview(result) }).catch(err => { if (current) setError(errorText(err)) })
    return () => { current = false }
  }, [revision, selected])
  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === 'visible') setRevision(value => value + 1) }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])
  const availability = new Map(overview?.availability.map(item => [item.employee_id, item]) || [])
  const needsSelection = (id: string) => ['not_requested', 'not_requested_or_stale', 'stale', 'ai_error'].includes(availability.get(id)?.state || '')
  const noStep = (id: string) => { const item = availability.get(id); return !!item && item.candidate_count === 0 && item.state !== 'requirements_covered' }
  const visible = (list?.items || []).filter(item => focus === 'all' || (focus === 'no_recommendation' ? needsSelection(item.employee_id) : noStep(item.employee_id)))
  if (selected) return <div className="hr-workspace"><button className="text-button back-to-team" onClick={() => setSearchParams({})}>← К списку команды</button><HrEmployeeReview key={selected} employeeId={selected} /></div>
  return <div className="hr-workspace team-page">
    <section className="page-title"><div><span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО HR</span><h1>Команда</h1><p>Найдите сотрудника, оцените разрывы и помогите выбрать следующий шаг.</p></div><Link className="button primary" to="/hr/import">Импортировать профили</Link></section>
    <section className="panel team-directory">
      <div className="team-toolbar"><label className="team-search">Поиск сотрудника<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Имя или ID сотрудника" /></label><label>Отдел<select value={department} onChange={event => setDepartment(event.target.value)}><option value="">Все отделы</option>{list?.departments.map(item => <option key={item}>{item}</option>)}</select></label><details className="team-more-filters"><summary>Ещё фильтры</summary><div><label>Роль<select value={role} onChange={event => setRole(event.target.value)}><option value="">Все</option>{list?.roles.map(item => <option key={item}>{item}</option>)}</select></label><label>Грейд<select value={grade} onChange={event => setGrade(event.target.value)}><option value="">Все</option>{list?.grades.map(item => <option key={item}>{item}</option>)}</select></label></div></details></div>
      <div className="tabs team-filters" role="tablist" aria-label="Фокус HR"><button role="tab" aria-selected={focus === 'all'} className={focus === 'all' ? 'active' : ''} onClick={() => setFocus('all')}>Все сотрудники <span>{list?.items.length ?? '—'}</span></button><button role="tab" aria-selected={focus === 'no_recommendation'} className={focus === 'no_recommendation' ? 'active' : ''} onClick={() => setFocus('no_recommendation')}>Нужен подбор <span>{overview ? list?.items.filter(item => needsSelection(item.employee_id)).length ?? 0 : '—'}</span></button><button role="tab" aria-selected={focus === 'no_step'} className={focus === 'no_step' ? 'active' : ''} onClick={() => setFocus('no_step')}>Нет доступного шага <span>{overview ? list?.items.filter(item => noStep(item.employee_id)).length ?? 0 : '—'}</span></button></div>
      {error && <div className="alert error" role="alert">{error}</div>}
      {loading && !list ? <p className="empty">Загружаем команду…</p> : <div className="table-wrap"><table className="team-table"><thead><tr><th>Сотрудник</th><th>Роль и грейд</th><th>Следующий шаг</th><th><span className="sr-only">Действие</span></th></tr></thead><tbody>{visible.map(item => { const state = availability.get(item.employee_id); return <tr key={item.employee_id}><td><div className="team-person"><span className="avatar">{item.full_name.split(' ').map(part => part[0]).slice(0, 2).join('')}</span><span><Link to={`/hr/employees?id=${encodeURIComponent(item.employee_id)}`}>{item.full_name}</Link><small>{item.department} · {item.employee_id}</small></span></div></td><td>{item.role}<small className="muted block">{item.grade}</small></td><td><span className={`team-state ${state?.state === 'recommended' ? 'ready' : ''}`}>{state ? availabilityLabels[state.state] || state.state : 'Загружаем статус…'}</span></td><td><Link className="button subtle" to={`/hr/employees?id=${encodeURIComponent(item.employee_id)}`}>Открыть</Link></td></tr> })}</tbody></table>{!visible.length && <p className="empty">По выбранным условиям сотрудников нет.</p>}</div>}
    </section>
    <Link className="team-analytics-link" to="/hr/competencies">Посмотреть разрывы компетенций и участие команды →</Link>
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

  return <NavigationShell user={user} onLogout={() => { setUser(null); navigate('/login', { replace: true }) }}><Routes>
    <Route path="/login" element={<Navigate to={user.role === 'hr' ? '/hr/employees' : '/employee'} replace />} />
    <Route path="/employee" element={user.role === 'employee' ? <ProfileView mode="employee" /> : <Navigate to="/hr/employees" replace />} />
    <Route path="/hr/employees" element={user.role === 'hr' ? <HrEmployees /> : <Navigate to="/employee" replace />} />
    <Route path="/hr/competencies" element={user.role === 'hr' ? <HrCompetencies /> : <Navigate to="/employee" replace />} />
    <Route path="/hr/import" element={user.role === 'hr' ? <HrImport /> : <Navigate to="/employee" replace />} />
    <Route path="*" element={<Navigate to={user.role === 'hr' ? '/hr/employees' : '/employee'} replace />} />
  </Routes></NavigationShell>
}
