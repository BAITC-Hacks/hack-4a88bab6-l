import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api'
import { ActivityChart, DynamicsChart, JournalSection, SkillChart } from './ProfileView'
import type { JournalFilter, JournalRow } from './ProfileView'
import type { Profile, RecSet } from './types'
import { availabilityLabels, dateLabel, dateTimeLabel, errorText, formatLabels, goalSourceLabels, numberLabel, statusLabels } from './ui'

type ReviewTab = 'gaps' | 'recommendations' | 'participation'

const exclusionLabels: Record<string, string> = {
  mandatory: 'Обязательные мероприятия учитываются отдельно',
  skipped: 'Предложения пропущены сотрудником',
  archived: 'Предложения убраны HR',
  role_or_grade: 'Не подходят роль или грейд',
  prerequisites: 'Не выполнены условия участия',
  completed: 'Неповторяемые мероприятия уже завершены',
  no_session: 'Нет доступной сессии',
  no_useful_gain: 'Нет полезного прироста для разрывов',
}

export default function HrEmployeeReview({ employeeId }: { employeeId: string }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [recs, setRecs] = useState<RecSet | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [tab, setTab] = useState<ReviewTab>('gaps')
  const [archiveFor, setArchiveFor] = useState('')
  const [archiveReason, setArchiveReason] = useState('')
  const [journalFilter, setJournalFilter] = useState<JournalFilter>('all')
  const [historyFrom, setHistoryFrom] = useState('')
  const [historyTo, setHistoryTo] = useState('')
  const [expandedHistory, setExpandedHistory] = useState(false)

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const next = await api.hrProfile(employeeId)
      setProfile(next)
      setRecs(next.recommendations)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [employeeId])

  useEffect(() => {
    setProfile(null)
    setRecs(null)
    setTab('gaps')
    setArchiveFor('')
    setArchiveReason('')
    setJournalFilter('all')
    setHistoryFrom('')
    setHistoryTo('')
    setExpandedHistory(false)
    void load()
  }, [load])

  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') void load(true) }
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [load])

  async function act(key: string, action: () => Promise<unknown>, message: string) {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      await action()
      await load(true)
      setNotice(message)
      return true
    } catch (err) {
      setError(errorText(err))
      return false
    } finally {
      setBusy('')
    }
  }

  const requiredSkills = useMemo(() => [...(profile?.skills ?? [])].filter(skill => skill.required > 0)
    .sort((a, b) => Number(b.critical && b.gap > 0) - Number(a.critical && a.gap > 0) || b.gap - a.gap || a.name.localeCompare(b.name, 'ru')), [profile])
  const gapSkills = requiredSkills.filter(skill => skill.gap > 0)
  const criticalSkills = gapSkills.filter(skill => skill.critical)
  const prioritySkills = gapSkills.slice(0, 5)
  const journalRows = useMemo<JournalRow[]>(() => {
    if (!profile) return []
    const activities: JournalRow[] = profile.history
      .filter(item => (!historyFrom || item.date >= historyFrom) && (!historyTo || item.date <= historyTo))
      .map(item => ({ kind: 'activity', key: `activity:${item.id}`, date: item.date, entry: item }))
    const skips: JournalRow[] = profile.skips
      .filter(item => {
        const date = (item.created_at || item.updated_at || '').slice(0, 10)
        return (!historyFrom || date >= historyFrom) && (!historyTo || date <= historyTo)
      })
      .map(item => ({ kind: 'skip', key: `skip:${item.event_id}`, date: (item.created_at || item.updated_at || '').slice(0, 10), entry: item }))
    return [...activities, ...skips].sort((a, b) => b.date.localeCompare(a.date) || a.key.localeCompare(b.key))
  }, [profile, historyFrom, historyTo])
  const active = useMemo(() => {
    if (!profile) return []
    return [
      ...profile.participations.filter(item => item.status === 'planned' || item.status === 'in_progress'),
      ...profile.history.filter(item => item.source === 'imported' && item.status === 'in_progress' && !item.linked_participation_id)
        .map(item => ({ id: `history:${item.id}`, event_id: item.event_id, title: item.title, status: item.status, session_date: item.date, is_simulated: false })),
    ]
  }, [profile])

  if (loading && !profile) return <div className="panel loading-panel">Загружаем профиль сотрудника…</div>
  if (!profile) return <div className="panel"><p className="error">{error || 'Профиль недоступен.'}</p><button className="button" onClick={() => void load()}>Повторить</button></div>

  const recStatus = recs?.status || 'not_requested'
  const recommendations = recs?.items ?? []
  const excluded = Object.entries(profile.exclusions ?? {}).filter(([, count]) => count > 0)
  const source = recs?.source === 'ai' ? 'Подбор AI' : recs?.source === 'fallback' ? 'Резервный подбор по правилам' : 'Подбор ещё не сформирован'
  const emptyReason = recStatus === 'not_requested'
    ? 'Подбор ещё не запрошен. Запустите расчёт, чтобы увидеть допустимые шаги.'
    : recStatus === 'stale'
      ? 'Профиль изменился. Пересчитайте рекомендации.'
      : recStatus === 'error'
        ? 'Подбор завершился ошибкой. Повторите запрос.'
        : profile.availability?.state === 'requirements_covered'
          ? 'Требования карьерной цели уже покрыты.'
          : recStatus === 'no_candidates'
            ? availabilityLabels[profile.availability?.state || ''] || 'На дату среза нет допустимых добровольных шагов.'
            : 'Пересчитайте подбор для проверки доступных шагов.'

  return <div className="hr-review">
    <header className="panel hr-review-header">
      <div>
        <Link className="text-button hr-review-back" to="/hr/employees">← К списку сотрудников</Link>
        <span className="eyebrow">HR-ПРОФИЛЬ · ОЦЕНКА СОТРУДНИКА</span>
        <h1>{profile.employee.full_name}</h1>
        <div className="hero-meta"><span>{profile.employee.employee_id}</span><span>{profile.employee.department}</span><span>{profile.employee.role} · {profile.employee.grade}</span></div>
      </div>
      <div className="hr-review-goal"><span className="eyebrow">КАРЬЕРНАЯ ЦЕЛЬ</span><strong>{profile.goal.role} · {profile.goal.grade}</strong><small>{goalSourceLabels[profile.goal.source] || profile.goal.source}</small><small>Дата среза: {dateLabel(profile.as_of_date)}</small></div>
    </header>

    {error && <div className="alert error" role="alert">{error}<button onClick={() => setError('')} aria-label="Закрыть">×</button></div>}
    {notice && <div className="alert success" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Закрыть">×</button></div>}

    <nav className="tabs hr-review-tabs" role="tablist" aria-label="Разделы HR-профиля">
      <button type="button" role="tab" aria-selected={tab === 'gaps'} className={tab === 'gaps' ? 'active' : ''} onClick={() => setTab('gaps')}>Анализ разрывов <span>{gapSkills.length}</span></button>
      <button type="button" role="tab" aria-selected={tab === 'recommendations'} className={tab === 'recommendations' ? 'active' : ''} onClick={() => setTab('recommendations')}>Рекомендации <span>{recommendations.length}</span></button>
      <button type="button" role="tab" aria-selected={tab === 'participation'} className={tab === 'participation' ? 'active' : ''} onClick={() => setTab('participation')}>Участие и история <span>{profile.history.length + profile.skips.length}</span></button>
    </nav>

    {tab === 'gaps' && <div className="hr-review-analysis">
      <div className="panel hr-review-summary"><div><span className="eyebrow">КАРТИНА ПО ЦЕЛИ</span><h2>{profile.critical_gaps ? `Критических разрывов: ${profile.critical_gaps}` : gapSkills.length ? 'Есть некритические разрывы' : 'Требования цели покрыты'}</h2><p className="muted">По оценке от {dateLabel(profile.employee.last_review_date)} и завершённым активностям после неё.</p></div><div className="hr-review-summary-facts"><span>Покрытие <strong>{profile.coverage_pct == null ? '—' : `${numberLabel(profile.coverage_pct, 1)}%`}</strong></span><span>Навыков с разрывом <strong>{gapSkills.length}</strong></span></div></div>
      <section className="panel hr-review-priority">
        <div className="section-heading"><div><span className="eyebrow">ПРИОРИТЕТЫ</span><h2>Что развивать первым</h2></div></div>
        {prioritySkills.length ? <div className="hr-review-priority-list">{prioritySkills.map(skill => <div className="hr-review-priority-item" key={skill.skill_id}><div><strong>{skill.name}</strong><small className="muted block">{skill.type === 'hard' ? 'Профессиональный навык' : 'Гибкий навык'}{skill.critical ? ' · критический разрыв' : ''}</small></div><span>{skill.current} → {skill.required}</span></div>)}</div> : <div className="empty">По требованиям текущей цели разрывов нет.</div>}
        {gapSkills.length > prioritySkills.length && <p className="hint">Остальные {gapSkills.length - prioritySkills.length} разрывов — в подробной таблице ниже.</p>}
      </section>
      <details className="hr-review-details"><summary>Подробные навыки и динамика</summary><div className="hr-review-details-content">
        <section className="panel"><div className="section-heading"><div><span className="eyebrow">ТРЕБОВАНИЯ ЦЕЛИ</span><h2>Все навыки</h2></div></div><div className="table-wrap"><table><thead><tr><th>Навык</th><th>Тип</th><th>Сейчас</th><th>Цель</th><th>Разрыв</th><th>Приоритет</th></tr></thead><tbody>{requiredSkills.map(skill => <tr key={skill.skill_id}><td><b>{skill.name}</b></td><td>{skill.type === 'hard' ? 'Hard' : 'Soft'}</td><td>{skill.current}</td><td>{skill.required}</td><td>{skill.gap}</td><td>{skill.critical && skill.gap > 0 ? 'Критический' : '—'}</td></tr>)}</tbody></table>{!requiredSkills.length && <div className="empty">Нет требований для этой цели.</div>}</div></section>
        <div className="two-col"><SkillChart title="Гибкие навыки" type="soft" skills={profile.skills} color="#8965c7" /><SkillChart title="Профессиональные навыки" type="hard" skills={profile.skills} color="#66758f" /></div>
        <DynamicsChart profile={profile} />
      </div></details>
      {criticalSkills.length === 0 && gapSkills.length > 0 && <p className="footnote">Текущие разрывы не отмечены как критические для цели.</p>}
    </div>}

    {tab === 'recommendations' && <div className="hr-review-recommendations">
      <section className="panel recommendation-section">
        <div className="section-heading"><div><span className="eyebrow">ПЛАН РАЗВИТИЯ</span><h2>Рекомендации для сотрудника</h2></div><button className="button primary" disabled={!!busy} onClick={() => void act('generate', () => api.generateHrRecommendations(employeeId), 'Рекомендации пересчитаны.')}>{busy === 'generate' ? 'Подбираем…' : 'Пересчитать подбор'}</button></div>
        <p className="hint">HR может убрать предложение из активного списка с указанием причины. Участие выбирает сотрудник.</p>
        <div className="recommendation-meta"><span className={`badge ${recs?.source === 'fallback' ? 'warning' : 'positive'}`}>{source}</span><span>{recs?.calculated_at ? `Обновлено ${dateTimeLabel(recs.calculated_at)}` : 'Пока не запрошено'}</span><span className="muted">{recStatus === 'stale' ? 'Данные изменились' : recStatus === 'no_candidates' ? 'Нет допустимых кандидатов' : ''}</span></div>
        <div className="card-list">{recommendations.length ? recommendations.map(item => <article className="recommendation-card" key={item.event_id}>
          <div className="rec-top"><div><h3>{item.title}</h3></div><span className="badge">{item.participation_status === 'planned' ? 'Запланировано' : item.action === 'continue' ? 'В работе' : 'Новый шаг'}</span></div>
          {item.develops.length > 0 && <p className="hr-review-rec-effect">Развивает: {item.develops.slice(0, 2).map(skill => `${skill.name} ${skill.current} → ${skill.after}`).join(', ')}{item.develops.length > 2 ? ` и ещё ${item.develops.length - 2}` : ''}</p>}
          <div className="rec-reason"><h4>Почему предложен этот шаг</h4><p>{item.rationale}</p></div>
          <details className="fact-details"><summary>Подробнее о выборе</summary><div className="tags"><span>{formatLabels[item.format] || item.format}</span><span>{numberLabel(item.duration_hours, 1)} ч</span><span>{item.format === 'self_paced' ? 'В своём темпе' : item.session_date ? `Сессия ${dateLabel(item.session_date)}` : 'Дата уточняется'}</span></div>{item.tradeoff && <p className="tradeoff"><b>Ограничение выбора:</b> {item.tradeoff}</p>}<ul>{item.factors.map(factor => <li key={factor.id}>{factor.label}</li>)}</ul></details>
          <div className="rec-actions">{item.action === 'continue' ? <span className="muted">Статус участия меняет сотрудник.</span> : archiveFor === item.event_id ? <div className="archive-form hr-review-archive-form"><label>Причина архивирования<input value={archiveReason} onChange={event => setArchiveReason(event.target.value)} maxLength={500} placeholder="Причина видна сотруднику" /></label><button className="button danger" disabled={!archiveReason.trim() || !!busy} onClick={() => void act(`archive-${item.event_id}`, () => api.archive(employeeId, item.event_id, archiveReason.trim()), 'Предложение сохранено в архиве HR.').then(ok => { if (ok) { setArchiveFor(''); setArchiveReason('') } })}>Сохранить</button><button className="button subtle" onClick={() => { setArchiveFor(''); setArchiveReason('') }}>Отмена</button></div> : <button className="button subtle" disabled={!!busy} onClick={() => { setArchiveFor(item.event_id); setArchiveReason('') }}>Убрать рекомендацию</button>}</div>
        </article>) : <div className="empty"><h3>Активных рекомендаций нет</h3><p>{emptyReason}</p>{profile.availability?.candidate_count != null && <p>Допустимых кандидатов по каталогу: <b>{profile.availability.candidate_count}</b></p>}{recStatus === 'no_candidates' && excluded.length > 0 && <div className="exclusion-summary"><strong>Причины исключения из подбора</strong><ul>{excluded.map(([reason, count]) => <li key={reason}>{exclusionLabels[reason] || reason}: {count}</li>)}</ul></div>}</div>}</div>
      </section>
      <section className="panel hr-review-archive-list"><div className="section-heading"><div><span className="eyebrow">АРХИВ HR</span><h2>Убранные предложения</h2></div><span className="muted">{profile.archives.length}</span></div>
        {profile.archives.length ? <div className="card-list">{profile.archives.map(item => <article className="compact-card" key={item.event_id}><div><strong>{item.title || 'Активность'}</strong><small>Причина HR: {item.reason}</small></div><button className="button subtle" disabled={!!busy} onClick={() => void act(`restore-${item.event_id}`, () => api.restoreArchive(employeeId, item.event_id), 'Предложение восстановлено из архива HR.')}>Восстановить</button></article>)}</div> : <p className="empty">Архивных предложений нет.</p>}
        <p className="hint">Архив можно отменить. Причина и история действий сохраняются на сервере.</p>
      </section>
    </div>}

    {tab === 'participation' && <div className="hr-review-participation">
      <section className="panel"><div className="section-heading"><div><span className="eyebrow">ТЕКУЩЕЕ УЧАСТИЕ</span><h2>Запланировано и в работе</h2></div><span className="muted">{active.length}</span></div>
        {active.length ? <div className="card-list">{active.map(item => <article className="participation-card" key={item.id}><div><span className="eyebrow">{statusLabels[item.status] || item.status}</span><h3>{item.title}</h3><p className="muted">{item.session_date ? `Сессия ${dateLabel(item.session_date)}` : 'В своём темпе'}{item.is_simulated ? ' · смоделировано' : ''}</p></div></article>)}</div> : <p className="empty">Нет запланированных или начатых активностей.</p>}
      </section>
      <ActivityChart rows={profile.history.filter(item => (!historyFrom || item.date >= historyFrom) && (!historyTo || item.date <= historyTo))} />
      <JournalSection rows={journalRows} filter={journalFilter} onFilter={setJournalFilter} from={historyFrom} onFrom={setHistoryFrom} to={historyTo} onTo={setHistoryTo} expanded={expandedHistory} onExpanded={setExpandedHistory} />
      <p className="footnote">Attended означает завершённое участие, Skipped — добровольный выбор сотрудника без штрафа. Неявка и отказ от обязательного назначения имеют отдельные статусы.</p>
    </div>}
  </div>
}
