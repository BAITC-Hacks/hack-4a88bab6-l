import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, PolarAngleAxis,
  PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from './api'
import type { History, Profile, RecSet, Skill } from './types'
import { availabilityLabels, dateLabel, dateTimeLabel, errorText, formatLabels, goalSourceLabels, numberLabel, statusLabels } from './ui'

const exclusionLabels: Record<string, string> = {
  mandatory: 'Обязательные мероприятия учитываются отдельно',
  skipped: 'Предложения пропущены сотрудником',
  archived: 'Предложения убраны HR',
  role_or_grade: 'Не подходят текущие роль или грейд',
  prerequisites: 'Не выполнены условия участия',
  completed: 'Неповторяемые мероприятия уже завершены',
  no_session: 'Нет сессии на дату среза или позже',
  no_useful_gain: 'Нет полезного прироста для текущих разрывов',
}

type Mode = 'employee' | 'hr'
type Tab = 'recommended' | 'chosen' | 'completed' | 'skipped' | 'archived'

function SkillChart({ title, type, skills, color }: { title: string; type: 'soft' | 'hard'; skills: Skill[]; color: string }) {
  const values = useMemo(() => skills.filter(s => s.type === type && s.required > 0)
    .sort((a, b) => Number(b.critical) - Number(a.critical) || b.gap - a.gap || b.required - a.required)
    .slice(0, 8), [skills, type])
  return <section className="panel skill-panel">
    <div className="section-heading"><div><span className={`eyebrow ${type}`}>{type === 'soft' ? 'SOFT SKILLS' : 'HARD SKILLS'}</span><h3>{title}</h3></div><span className="muted">Шкала 0–5</span></div>
    {values.length >= 3 ? <div className="chart radar-chart">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={values.map(s => ({ name: s.name, current: s.current, required: s.required }))} outerRadius="65%">
          <PolarGrid stroke="#dfe7ef" />
          <PolarAngleAxis dataKey="name" tick={{ fill: '#526277', fontSize: 11 }} />
          <PolarRadiusAxis domain={[0, 5]} tickCount={6} tick={{ fill: '#8394a0', fontSize: 10 }} />
          <Radar name="Текущий уровень" dataKey="current" stroke={color} fill={color} fillOpacity={0.25} strokeWidth={2} isAnimationActive={false} />
          <Radar name="Требование цели" dataKey="required" stroke="#d79c52" fill="#d79c52" fillOpacity={0.08} strokeWidth={2} isAnimationActive={false} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Tooltip />
        </RadarChart>
      </ResponsiveContainer>
    </div> : values.length ? <div className="skill-bars">{values.map(s => <div key={s.skill_id}><div className="flex-between"><span>{s.name}</span><b>{s.current} / {s.required}</b></div><div className="bar-track"><span style={{ width: `${s.current / 5 * 100}%`, background: color }} /></div></div>)}</div> : <p className="empty">В выбранной цели нет требований к этой группе навыков.</p>}
  </section>
}

function DynamicsChart({ profile }: { profile: Profile }) {
  const keys = profile.skills.filter(s => s.required > 0).sort((a, b) => Number(b.critical) - Number(a.critical) || b.gap - a.gap).slice(0, 4)
  const data = profile.dynamics.map(point => ({ date: dateLabel(point.date), ...point.skills }))
  const colors = ['#137b6a', '#406fe2', '#df9a47', '#9a65c8']
  return <section className="panel">
    <div className="section-heading"><div><span className="eyebrow">ПРОГРЕСС</span><h3>Расчётная динамика после последней оценки</h3></div></div>
    <p className="hint">Отправная точка — оценка от {dateLabel(profile.employee.last_review_date)}. Учитываются завершения после неё до даты среза.</p>
    {data.length > 1 && keys.length ? <div className="chart line-chart"><ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 12, right: 12, left: -26, bottom: 4 }}>
        <CartesianGrid stroke="#e8edf3" strokeDasharray="3 3" /><XAxis dataKey="date" tick={{ fontSize: 11 }} /><YAxis domain={[0, 5]} ticks={[0, 1, 2, 3, 4, 5]} tick={{ fontSize: 11 }} />
        <Tooltip /><Legend wrapperStyle={{ fontSize: 11 }} />
        {keys.map((s, index) => <Line key={s.skill_id} name={s.name} dataKey={s.skill_id} stroke={colors[index]} strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />)}
      </LineChart>
    </ResponsiveContainer></div> : <div className="empty chart-empty">Пока есть только точка последней оценки. Изменения появятся после завершённой активности.</div>}
  </section>
}

function ActivityChart({ rows }: { rows: History[] }) {
  const data = useMemo(() => {
    const months = new Map<string, { month: string; completed: number; active: number; other: number }>()
    for (const item of rows) {
      const month = item.date.slice(0, 7)
      if (!months.has(month)) months.set(month, { month, completed: 0, active: 0, other: 0 })
      const point = months.get(month)!
      if (item.status === 'completed') point.completed++
      else if (item.status === 'in_progress') point.active++
      else point.other++
    }
    return [...months.values()].sort((a, b) => a.month.localeCompare(b.month)).slice(-12)
  }, [rows])
  return <section className="panel">
    <div className="section-heading"><div><span className="eyebrow">АКТИВНОСТЬ</span><h3>Участие по месяцам</h3></div></div>
    <p className="hint">Количество записей за выбранный период. Для самостоятельных курсов дата исходной записи может означать зачисление.</p>
    {data.length ? <div className="chart line-chart"><ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 12, right: 8, left: -26, bottom: 4 }}>
        <CartesianGrid stroke="#e8edf3" strokeDasharray="3 3" /><XAxis dataKey="month" tick={{ fontSize: 11 }} /><YAxis allowDecimals={false} tick={{ fontSize: 11 }} /><Tooltip /><Legend wrapperStyle={{ fontSize: 11 }} />
        <Bar dataKey="completed" name="Завершено" stackId="a" fill="#137b6a" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="active" name="В работе" stackId="a" fill="#406fe2" isAnimationActive={false} />
        <Bar dataKey="other" name="Другие статусы" stackId="a" fill="#c9d3e2" isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer></div> : <div className="empty chart-empty">За выбранный период записей нет.</div>}
  </section>
}

function ActivityHeatmap({ calendar }: { calendar: NonNullable<Profile['activity_calendar']> }) {
  const byDate = new Map(calendar.days.map(day => [day.date, day]))
  const start = new Date(`${calendar.from}T00:00:00Z`)
  const end = new Date(`${calendar.to}T00:00:00Z`)
  const padding = (start.getUTCDay() + 6) % 7
  const cells: ({ date: string; count: number; estimated_count: number; simulated_count: number } | null)[] = Array.from({ length: padding }, () => null)
  for (let cursor = new Date(start); cursor <= end; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    const date = cursor.toISOString().slice(0, 10)
    cells.push(byDate.get(date) || { date, count: 0, estimated_count: 0, simulated_count: 0 })
  }
  const estimated = calendar.days.reduce((sum, day) => sum + day.estimated_count, 0)
  const simulated = calendar.days.reduce((sum, day) => sum + day.simulated_count, 0)
  return <section className="panel heatmap-panel">
    <div className="section-heading"><div><span className="eyebrow">ЛИЧНАЯ АКТИВНОСТЬ</span><h3>365 дней развития</h3></div><span className="heatmap-total">{calendar.total} завершённых добровольных активностей</span></div>
    <p className="hint">Каждая клетка — день расчётного участия в периоде {dateLabel(calendar.from)} — {dateLabel(calendar.to)}. Пропуски, неявки и обязательные назначения сюда не входят.</p>
    <div className="heatmap-overflow"><div className="heatmap-content"><div className="heatmap-weekdays"><span>Пн</span><span>Ср</span><span>Пт</span></div><div className="heatmap-grid" role="img" aria-label={`Календарь добровольных завершений: ${calendar.total} за 365 дней`}>
      {cells.map((day, index) => day ? <span key={day.date} className={`heatmap-cell intensity-${Math.min(4, day.count)}`} title={`${dateLabel(day.date)}: ${day.count} завершено${day.estimated_count ? `, из них ${day.estimated_count} с приблизительной датой` : ''}${day.simulated_count ? `, ${day.simulated_count} смоделировано` : ''}`} /> : <span key={`pad-${index}`} className="heatmap-spacer" />)}
    </div></div></div>
    <div className="heatmap-footer"><span>{estimated ? `${estimated} отметок по приблизительной дате self-paced CSV.` : 'Даты исходных self-paced завершений могут быть приблизительными.'}{simulated ? ` ${simulated} завершений смоделировано в демо.` : ''}</span><span className="heatmap-legend">Реже <i className="intensity-0" /><i className="intensity-1" /><i className="intensity-2" /><i className="intensity-3" /><i className="intensity-4" /> Чаще</span></div>
  </section>
}

export default function ProfileView({ mode, employeeId }: { mode: Mode; employeeId?: string }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [recs, setRecs] = useState<RecSet | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [tab, setTab] = useState<Tab>('recommended')
  const [expandedHistory, setExpandedHistory] = useState(false)
  const [historyFrom, setHistoryFrom] = useState('')
  const [historyTo, setHistoryTo] = useState('')
  const [editGoal, setEditGoal] = useState(false)
  const [goalRole, setGoalRole] = useState('')
  const [goalGrade, setGoalGrade] = useState('')
  const [profiles, setProfiles] = useState<{ role: string; grade: string }[]>([])
  const [archiveFor, setArchiveFor] = useState('')
  const [archiveReason, setArchiveReason] = useState('')

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const next = mode === 'hr' ? await api.hrProfile(employeeId!) : await api.employeeProfile()
      setProfile(next)
      setRecs(next.recommendations)
      setGoalRole(next.goal.role)
      setGoalGrade(next.goal.grade)
    } catch (err) { setError(errorText(err)) }
    finally { setLoading(false) }
  }, [mode, employeeId])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === 'visible') void load(true) }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => { window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus) }
  }, [load])
  useEffect(() => {
    if (mode === 'employee') api.roleProfiles().then(data => setProfiles(data.items)).catch(() => {})
  }, [mode])

  async function act(key: string, action: () => Promise<unknown>, message: string, nextTab?: Tab): Promise<boolean> {
    setBusy(key); setError(''); setNotice('')
    try {
      await action()
      await load(true)
      setNotice(message)
      if (nextTab) setTab(nextTab)
      return true
    } catch (err) { setError(errorText(err)); return false }
    finally { setBusy('') }
  }

  const availableGrades = [...new Set(profiles.filter(p => p.role === goalRole).map(p => p.grade))]
  const filteredHistory = (profile?.history ?? []).filter(item => (!historyFrom || item.date >= historyFrom) && (!historyTo || item.date <= historyTo))
    .sort((a, b) => b.date.localeCompare(a.date))
  const completed = filteredHistory.filter(item => item.status === 'completed')
  const active = [
    ...(profile?.participations ?? []).filter(item => ['in_progress', 'planned'].includes(item.status)),
    ...(profile?.history ?? []).filter(item => item.source === 'imported' && item.status === 'in_progress' && !item.linked_participation_id)
      .map(item => ({ id: `history:${item.id}`, event_id: item.event_id, title: item.title, status: item.status, session_date: item.date, is_simulated: false })),
  ]
  const recommendations = recs?.items ?? []
  const skippedEvents = new Set(profile?.skips.map(item => item.event_id) ?? [])
  const archivedEvents = new Set(profile?.archives.map(item => item.event_id) ?? [])
  const recSource = recs?.source === 'ai' ? 'Подбор AI' : recs?.source === 'fallback' ? 'Резервный подбор по правилам: AI недоступен' : 'Рекомендации не сформированы'
  const recStatus = recs?.status || 'not_requested'
  const excluded = Object.entries(profile?.exclusions ?? {}).filter(([, count]) => count > 0)
  const candidateCount = profile?.availability?.candidate_count

  if (loading && !profile) return <div className="panel loading-panel">Загружаем профиль…</div>
  if (!profile) return <div className="panel"><p className="error">{error || 'Профиль недоступен.'}</p><button className="button" onClick={() => void load()}>Повторить</button></div>

  return <div className="profile-view">
    <section className="profile-hero panel">
      <div>
        <span className="eyebrow">{mode === 'hr' ? 'ПРОФИЛЬ СОТРУДНИКА' : 'ЛИЧНЫЙ КАБИНЕТ'}</span>
        <h1>{profile.employee.full_name}</h1>
        <div className="hero-meta"><span>{profile.employee.employee_id}</span><span>{profile.employee.department}</span><span>{profile.employee.role} · {profile.employee.grade}</span></div>
      </div>
      <div className="hero-goal"><span className="eyebrow">КАРЬЕРНАЯ ТРАЕКТОРИЯ</span><strong>{profile.goal.role} · {profile.goal.grade}</strong><small>{goalSourceLabels[profile.goal.source] || profile.goal.source}</small>
        {mode === 'employee' && <button className="text-button" onClick={() => setEditGoal(!editGoal)}>{editGoal ? 'Отмена' : 'Подтвердить или изменить цель'}</button>}
      </div>
    </section>

    {editGoal && mode === 'employee' && <section className="panel goal-editor">
      <h3>Выбор цели</h3><p className="hint">Цель сохраняется отдельно от исходного профиля. Грейд не изменяется автоматически после обучения.</p>
      <div className="form-row"><label>Роль<select value={goalRole} onChange={e => { setGoalRole(e.target.value); setGoalGrade(profiles.find(p => p.role === e.target.value)?.grade || '') }}>
        {[...new Set(profiles.map(p => p.role))].map(role => <option key={role} value={role}>{role}</option>)}
      </select></label><label>Грейд<select value={goalGrade} onChange={e => setGoalGrade(e.target.value)}>{availableGrades.map(grade => <option key={grade}>{grade}</option>)}</select></label>
        <button className="button primary align-end" disabled={!goalRole || !goalGrade || !!busy} onClick={() => void act('goal', () => api.setGoal(goalRole, goalGrade), 'Цель сохранена. Рекомендации нужно обновить.').then(ok => { if (ok) setEditGoal(false) })}>Сохранить цель</button></div>
    </section>}

    <div className="info-strip"><span>Дата среза: <b>{dateLabel(profile.as_of_date)}</b></span>{profile.demo_mode && <span className="demo-chip">Демонстрационный режим</span>}<span>Рекомендации добровольные. Пропуск не уменьшает ваши навыки.</span></div>
    {error && <div className="alert error" role="alert">{error}<button onClick={() => setError('')} aria-label="Закрыть">×</button></div>}
    {notice && <div className="alert success" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Закрыть">×</button></div>}

    <div className="stats-grid">
      <div className="panel metric"><span>Покрытие требований цели</span><strong>{profile.coverage_pct == null ? '—' : `${numberLabel(profile.coverage_pct, 1)}%`}</strong><small>{profile.coverage_pct == null ? 'Недостаточно требований для расчёта' : 'Расчёт по требуемым навыкам, не вероятность повышения'}</small></div>
      <div className="panel metric"><span>Незакрытые критические навыки</span><strong>{profile.critical_gaps}</strong><small>По требованиям выбранной цели</small></div>
      <div className="panel metric"><span>Последняя оценка</span><strong className="date-metric">{dateLabel(profile.employee.last_review_date)}</strong><small>Исходная точка расчётной динамики</small></div>
    </div>

    <div className="two-col"><SkillChart title="Гибкие навыки" type="soft" skills={profile.skills} color="#8965c7" /><SkillChart title="Профессиональные навыки" type="hard" skills={profile.skills} color="#137b6a" /></div>
    <details className="panel details-panel"><summary>Все навыки выбранной цели <span className="muted">{profile.skills.filter(s => s.required > 0).length}</span></summary>
      <div className="table-wrap"><table><thead><tr><th>Навык</th><th>Тип</th><th>Сейчас</th><th>Требуется</th><th>Разрыв</th><th>Критический</th></tr></thead><tbody>{profile.skills.filter(s => s.required > 0).sort((a,b) => b.gap - a.gap).map(s => <tr key={s.skill_id}><td><b>{s.name}</b><small className="muted block">{s.skill_id}</small></td><td>{s.type === 'hard' ? 'Hard' : 'Soft'}</td><td>{s.current}</td><td>{s.required}</td><td><span className={s.gap ? 'gap' : 'ok'}>{s.gap}</span></td><td>{s.critical ? 'Да' : '—'}</td></tr>)}</tbody></table></div>
    </details>

    <div className="two-col"><DynamicsChart profile={profile} /><ActivityChart rows={filteredHistory} /></div>
    {mode === 'employee' && profile.activity_calendar && <ActivityHeatmap calendar={profile.activity_calendar} />}

    <section className="panel recommendation-section" id="recommendations">
      <div className="section-heading"><div><span className="eyebrow">СЛЕДУЮЩИЕ ШАГИ</span><h2>Рекомендации и участие</h2></div>
        <button className="button primary" disabled={!!busy} onClick={() => void act('generate', async () => { const result = mode === 'hr' ? await api.generateHrRecommendations(employeeId!) : await api.generateEmployeeRecommendations(); setRecs(result) }, 'Подбор обновлён.')}>{busy === 'generate' ? 'Подбираем…' : mode === 'hr' ? 'Пересчитать' : 'Подобрать шаги'}</button></div>
      <div className="recommendation-meta"><span className={`badge ${recs?.source === 'fallback' ? 'warning' : 'positive'}`}>{recSource}</span><span>{recs?.calculated_at ? `Обновлено ${dateTimeLabel(recs.calculated_at)}` : 'Пока не запрошено'}</span><span className="muted">{({ stale: 'Данные изменились', not_requested: 'Ещё не запрошено', no_candidates: 'Нет допустимых кандидатов', fallback: 'Резервный режим' } as Record<string, string>)[recStatus] || ''}</span></div>
      <div className="tabs" role="tablist" aria-label="Состояние активностей">
        {([['recommended', 'Рекомендовано', recommendations.length], ['chosen', 'Выбрано / в работе', active.length], ['completed', 'Выполнено', completed.length], ['skipped', 'Пропущено', profile.skips.length], ['archived', 'Архив HR', profile.archives.length]] as [Tab, string, number][]).map(([id, label, count]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label} <span>{count}</span></button>)}
      </div>

      {tab === 'recommended' && <div className="card-list">{recommendations.length ? recommendations.map(item => <article className="recommendation-card" key={item.event_id}>
        <div className="rec-top"><div><span className="eyebrow">{item.event_id} · {item.type}</span><h3>{item.title}</h3></div><span className="badge">{item.participation_status === 'planned' ? 'Запланировано' : item.action === 'continue' ? 'Продолжить' : 'Новый шаг'}</span></div>
        <div className="tags"><span>{formatLabels[item.format] || item.format}</span><span>{numberLabel(item.duration_hours, 1)} ч</span><span>{item.format === 'self_paced' ? 'В своём темпе' : item.session_date ? `Сессия ${dateLabel(item.session_date)}` : 'Дата уточняется'}</span></div>
        {item.develops.length > 0 && <div className="gains">{item.develops.map(s => <span key={s.skill_id}>{s.name}: <b>{s.current} → {s.after}</b>{s.required > 0 && <small> цель {s.required}</small>}</span>)}</div>}
        <p>{item.rationale}</p>{item.tradeoff && <p className="tradeoff"><b>Ограничение выбора:</b> {item.tradeoff}</p>}
        <details className="fact-details"><summary>Факты и источники обоснования ({item.factors.length})</summary><ul>{item.factors.map(f => <li key={f.id}>{f.label} <small>{f.source}</small></li>)}</ul></details>
        <div className="rec-actions">{mode === 'employee' ? <>
          {item.action === 'start' && <button className="button primary" disabled={!!busy} onClick={() => void act(`start-${item.event_id}`, () => api.start(item.event_id, item.session_date), item.session_date ? 'Участие запланировано.' : 'Участие начато.', 'chosen')}>{busy === `start-${item.event_id}` ? 'Сохраняем…' : item.session_date ? 'Запланировать' : 'Начать'}</button>}
          {item.action === 'continue' && <button className="button primary" onClick={() => setTab('chosen')}>Перейти к участию</button>}
          <button className="button subtle" disabled={!!busy} onClick={() => void act(`skip-${item.event_id}`, () => api.skip(item.event_id), 'Предложение пропущено без штрафа.', 'skipped')}>Пропустить</button>
        </> : <>
          {item.action === 'continue' ? <span className="muted">Участие уже начато; его статус меняет только сотрудник.</span> : archiveFor === item.event_id ? <div className="archive-form"><label>Причина архивирования<input value={archiveReason} onChange={e => setArchiveReason(e.target.value)} maxLength={500} placeholder="Укажите причину для сотрудника" /></label><button className="button danger" disabled={!archiveReason.trim() || !!busy} onClick={() => void act(`archive-${item.event_id}`, () => api.archive(employeeId!, item.event_id, archiveReason.trim()), 'Предложение убрано из активных и сохранено в архиве.', 'archived').then(ok => { if (ok) { setArchiveFor(''); setArchiveReason('') } })}>Сохранить</button><button className="button subtle" onClick={() => setArchiveFor('')}>Отмена</button></div> : <button className="button subtle" onClick={() => { setArchiveFor(item.event_id); setArchiveReason('') }}>Убрать рекомендацию</button>}
        </>}</div>
      </article>) : <div className="empty"><h3>Активных рекомендаций нет</h3><p>{recStatus === 'not_requested' ? 'Подбор ещё не запрошен. Нажмите «Подобрать шаги».' : recStatus === 'stale' ? 'Данные изменились. Обновите подбор, чтобы получить актуальные предложения.' : recStatus === 'error' ? 'Не удалось получить подбор. Повторите запрос.' : profile.availability?.state === 'requirements_covered' ? 'Требования цели уже покрыты. Повышение грейда не происходит автоматически.' : recStatus === 'no_candidates' ? availabilityLabels[profile.availability?.state || ''] || 'Сейчас нет допустимых добровольных шагов.' : 'Обновите подбор для проверки доступных шагов.'}</p>{candidateCount != null && <p className="candidate-count">Допустимых кандидатов по каталогу: <b>{candidateCount}</b></p>}{recStatus === 'no_candidates' && excluded.length > 0 && <div className="exclusion-summary"><strong>Причины исключения из подбора</strong><ul>{excluded.map(([reason, count]) => <li key={reason}>{exclusionLabels[reason] || reason}: {count}</li>)}</ul></div>}</div>}</div>}

      {tab === 'chosen' && <div className="card-list">{active.length ? active.map(item => <article className="participation-card" key={item.id}><div><span className="eyebrow">{item.event_id} · {statusLabels[item.status] || item.status}</span><h3>{item.title}</h3><p className="muted">{item.session_date ? `Сессия ${dateLabel(item.session_date)}` : 'В своём темпе'}{item.is_simulated ? ' · учебная демонстрация' : ''}</p></div>{mode === 'employee' && <div className="rec-actions">{(profile.demo_mode || !item.session_date || item.session_date <= profile.as_of_date) && <button className="button primary" disabled={!!busy} onClick={() => void act(`complete-${item.id}`, () => api.complete(item.id, Boolean(profile.demo_mode && item.session_date && item.session_date > profile.as_of_date)), 'Выполнение сохранено; навыки пересчитаны.', 'completed')}>{profile.demo_mode && item.session_date && item.session_date > profile.as_of_date ? 'Смоделировать выполнение' : 'Отметить выполненной'}</button>}<button className="button subtle" disabled={!!busy} onClick={() => void act(`stop-${item.id}`, () => api.stop(item.id), 'Участие прекращено и сохранено в истории.')}>Прекратить участие</button></div>}</article>) : <div className="empty">Пока нет выбранных или начатых активностей.</div>}</div>}

      {tab === 'completed' && <div className="card-list">{completed.length ? completed.map(item => <article className="compact-card" key={item.id}><div><strong>{item.title}</strong><small>{item.event_id} · {dateLabel(item.date)}{item.mandatory ? ' · обязательное' : ''}{item.is_simulated ? ' · смоделировано' : ''}</small></div><span className="badge positive">Выполнено</span></article>) : <div className="empty">В выбранном периоде завершённых активностей нет.</div>}</div>}

      {tab === 'skipped' && <div className="card-list">{profile.skips.length ? profile.skips.map(item => <article className="compact-card" key={item.event_id}><div><strong>{item.title || item.event_id}</strong><small>{item.event_id} · {item.reason || 'Причина не указана'} · Добровольный пропуск не меняет навыки</small></div>{mode === 'employee' && <button className="button subtle" disabled={!!busy} onClick={() => void act(`restore-${item.event_id}`, () => api.restoreSkip(item.event_id), 'Предложение возвращено в рассмотрение. Обновите подбор.', 'recommended')}>Вернуть в рассмотрение</button>}</article>) : <div className="empty">Пропущенных предложений нет.</div>}</div>}

      {tab === 'archived' && <div className="card-list">{profile.archives.length ? profile.archives.map(item => <article className="compact-card" key={item.event_id}><div><strong>{item.title || item.event_id}</strong><small>{item.event_id} · Причина HR: {item.reason}</small></div>{mode === 'hr' && <button className="button subtle" disabled={!!busy} onClick={() => void act(`unarchive-${item.event_id}`, () => api.restoreArchive(employeeId!, item.event_id), 'Архив восстановлен. Пропуск сотрудника, если он был, остаётся.', 'recommended')}>Восстановить</button>}</article>) : <div className="empty">Архивных предложений нет.</div>}</div>}
    </section>

    <section className="panel history-section"><div className="section-heading"><div><span className="eyebrow">ЖУРНАЛ</span><h2>История участия</h2></div><span className="muted">{filteredHistory.length} записей</span></div>
      <div className="filters"><label>С<input type="date" value={historyFrom} onChange={e => setHistoryFrom(e.target.value)} /></label><label>По<input type="date" value={historyTo} onChange={e => setHistoryTo(e.target.value)} /></label>{(historyFrom || historyTo) && <button className="text-button" onClick={() => { setHistoryFrom(''); setHistoryTo('') }}>Сбросить период</button>}</div>
      <div className="table-wrap"><table><thead><tr><th>Активность</th><th>Дата записи</th><th>Статус</th><th>Прогресс</th><th>Источник</th></tr></thead><tbody>{filteredHistory.slice(0, expandedHistory ? undefined : 12).map(item => <tr key={item.id}><td><b>{item.title}</b><small className="muted block">{item.event_id}{item.mandatory ? ' · обязательное' : ''}{item.is_simulated ? ' · смоделировано' : ''}</small></td><td>{dateLabel(item.date)}{item.date_meaning === 'enrollment_or_assignment' && <small className="muted block">дата зачисления</small>}</td><td><span className={`status ${item.status}`}>{statusLabels[item.status] || item.status}</span></td><td>{item.completion_pct}%</td><td>{item.source === 'imported' ? 'Исходный журнал' : 'В приложении'}</td></tr>)}</tbody></table>{!filteredHistory.length && <div className="empty">Записей за этот период нет.</div>}</div>
      {filteredHistory.length > 12 && <button className="text-button" onClick={() => setExpandedHistory(!expandedHistory)}>{expandedHistory ? 'Свернуть' : `Показать все ${filteredHistory.length}`}</button>}
      <p className="hint">Для self-paced мероприятий дата исходной записи может быть датой зачисления. Точный день завершения до последней оценки неизвестен.</p>
    </section>
    {(skippedEvents.size > 0 || archivedEvents.size > 0) && <p className="footnote">Пропуск сотрудника и архив HR хранятся независимо. Исторические записи участия остаются доступными.</p>}
  </div>
}
