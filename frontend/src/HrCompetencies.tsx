import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api'
import { DepartmentHeatmap, GapBars, ParticipationSummary } from './HrCharts'
import type { Competencies, EmployeeList } from './types'
import { availabilityLabels, dateTimeLabel, errorText, goalSourceLabels, numberLabel } from './ui'

const coverageLabels: Record<string, string> = {
  none_in_catalog: 'В каталоге нет мероприятия для этого навыка',
  not_in_catalog: 'В каталоге нет мероприятия для этого навыка',
  no_event: 'В каталоге нет мероприятия для этого навыка',
  unavailable: 'Есть мероприятие, но сейчас не подходит сотруднику',
  not_eligible: 'Есть мероприятие, но сейчас не подходит сотруднику',
  partial: 'Есть частичный прирост, но потолка недостаточно',
  partial_ceiling: 'Есть частичный прирост, но потолка недостаточно',
  ceiling_reached: 'Потолок мероприятия уже достигнут',
  catalogued: 'Есть в каталоге; допуск проверяется для сотрудника',
  full: 'Есть шаг для закрытия разрыва',
  available: 'Есть подходящий шаг',
  covered: 'Требование покрыто',
}

type CompetencySection = 'gaps' | 'next' | 'participation'

export default function HrCompetencies() {
  const [data, setData] = useState<Competencies | null>(null)
  const [options, setOptions] = useState<EmployeeList | null>(null)
  const [department, setDepartment] = useState('')
  const [role, setRole] = useState('')
  const [grade, setGrade] = useState('')
  const [type, setType] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [includeMandatory, setIncludeMandatory] = useState(false)
  const [expanded, setExpanded] = useState('')
  const [activeSection, setActiveSection] = useState<CompetencySection>('gaps')
  const [showAllSkills, setShowAllSkills] = useState(false)
  const [showSkillTable, setShowSkillTable] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    const params = new URLSearchParams()
    if (department) params.set('department', department)
    if (role) params.set('role', role)
    if (grade) params.set('grade', grade)
    if (type) params.set('type', type)
    if (dateFrom) params.set('date_from', dateFrom)
    if (dateTo) params.set('date_to', dateTo)
    params.set('mandatory', 'all')
    try { setData(await api.competencies(params)) }
    catch (err) { setError(errorText(err)) }
    finally { setLoading(false) }
  }, [department, role, grade, type, dateFrom, dateTo])

  useEffect(() => { void load() }, [load, revision])
  useEffect(() => { api.employees(new URLSearchParams()).then(setOptions).catch(() => {}) }, [revision])
  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === 'visible') setRevision(value => value + 1) }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  const skills = useMemo(() => [...(data?.skills ?? [])].sort((a, b) => b.gap_count - a.gap_count || b.critical_count - a.critical_count), [data])
  const displayedSkills = showAllSkills ? skills : skills.slice(0, 10)
  const visibleParticipation = (data?.participation ?? []).filter(item => includeMandatory || !item.mandatory)
  const gapPeople = data?.availability.filter(item => item.state !== 'recommended' && item.state !== 'active' && item.state !== 'available') ?? []
  const gapTotal = data?.skills.reduce((sum, item) => sum + item.gap_count, 0) ?? 0

  return <div className="competencies-page">
    <section className="page-title"><div><span className="eyebrow">HR · АНАЛИТИКА</span><h1>Развитие команды</h1><p>Разрывы навыков, доступные шаги и результаты участия.</p></div><Link className="button primary" to="/hr/import">Импорт данных</Link></section>
    {error && <div className="alert error" role="alert">{error} <button className="text-button" onClick={() => void load()}>Повторить</button></div>}
    <section className="panel filter-panel"><div className="section-heading"><h2>Фильтры</h2><button className="text-button" onClick={() => { setDepartment(''); setRole(''); setGrade(''); setType(''); setDateFrom(''); setDateTo('') }}>Сбросить</button></div>
      <div className="filters"><label>Отдел<select value={department} onChange={e => setDepartment(e.target.value)}><option value="">Все отделы</option>{options?.departments.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Текущая роль<select value={role} onChange={e => setRole(e.target.value)}><option value="">Все роли</option>{options?.roles.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Грейд<select value={grade} onChange={e => setGrade(e.target.value)}><option value="">Все грейды</option>{options?.grades.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Тип навыка<select value={type} onChange={e => setType(e.target.value)}><option value="">Hard и soft</option><option value="hard">Hard</option><option value="soft">Soft</option></select></label>
        <label>Период с<input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label><label>Период по<input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} /></label></div>
      <p className="hint">Период применяется к участию по активностям; разрывы навыков считаются на дату среза.</p>
    </section>

    <nav className="tabs" aria-label="Разделы HR-аналитики">
      <button type="button" className={activeSection === 'gaps' ? 'active' : ''} aria-pressed={activeSection === 'gaps'} onClick={() => setActiveSection('gaps')}>Разрывы навыков</button>
      <button type="button" className={activeSection === 'next' ? 'active' : ''} aria-pressed={activeSection === 'next'} onClick={() => setActiveSection('next')}>Следующий шаг</button>
      <button type="button" className={activeSection === 'participation' ? 'active' : ''} aria-pressed={activeSection === 'participation'} onClick={() => setActiveSection('participation')}>Участие</button>
    </nav>

    {loading && !data ? <div className="panel loading-panel">Считаем показатели по текущим данным…</div> : data && <>
      {activeSection === 'gaps' && <>
        <div className="hr-stat-overview"><GapBars skills={data.skills} onSelect={id => { setExpanded(id); setShowAllSkills(true); setShowSkillTable(true); window.setTimeout(() => document.getElementById('skill-details')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50) }} /><DepartmentHeatmap skills={data.skills} cells={data.department_skills || []} onSelect={(id, selectedDepartment) => { setDepartment(selectedDepartment); setExpanded(id); setShowAllSkills(true); setShowSkillTable(true) }} /></div>
        <details id="skill-details" className="analytics-details" open={showSkillTable} onToggle={event => setShowSkillTable(event.currentTarget.open)}><summary>Подробные данные по навыкам</summary><section className="panel"><div className="section-heading"><div><span className="eyebrow">РАЗРЫВЫ НАВЫКОВ</span><h2>Где требуется развитие</h2></div><span className="muted">{loading ? 'Обновляем… · ' : ''}{skills.filter(s => s.gap_count > 0).length} навыков с разрывом из {skills.length} · {gapTotal} пар «сотрудник — навык»</span></div>
        <p className="hint">Доля = сотрудники с разрывом / сотрудники, для чьей цели требуется навык. Нажмите на строку для подробностей.</p>
        <div className="table-wrap"><table><thead><tr><th>Навык</th><th>Тип</th><th>Требуется людям</th><th>С разрывом</th><th>Доля</th><th>Критических</th></tr></thead>{displayedSkills.map(skill => <tbody key={skill.skill_id}><tr className="clickable-row" onClick={() => setExpanded(expanded === skill.skill_id ? '' : skill.skill_id)}><td><b>{skill.name}</b><small className="muted block">{skill.skill_id}</small></td><td>{skill.type === 'hard' ? 'Hard' : 'Soft'}</td><td>{skill.required_count}</td><td><b className={skill.gap_count ? 'gap' : 'ok'}>{skill.gap_count}</b></td><td>{skill.gap_pct == null ? '—' : `${numberLabel(skill.gap_pct, 1)}%`}</td><td>{skill.critical_count}</td></tr>{expanded === skill.skill_id && <tr className="expanded-row"><td colSpan={6}><div className="skill-detail"><div><h3>Затронутые сотрудники</h3>{skill.people.length ? <div className="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Сейчас</th><th>Цель</th><th>Разрыв</th><th>Каталог</th></tr></thead><tbody>{skill.people.map(person => <tr key={person.employee_id}><td><Link to={`/hr/employees?id=${encodeURIComponent(person.employee_id)}`}>{person.full_name}</Link><small className="muted block">{person.employee_id}</small></td><td>{person.current}</td><td>{person.required}</td><td>{person.gap}</td><td>{coverageLabels[person.coverage_status] || person.coverage_status}</td></tr>)}</tbody></table></div> : <p className="empty">Нет сотрудников с разрывом по фильтру.</p>}</div><div><h3>Мероприятия каталога</h3>{skill.events.length ? <ul className="event-list">{skill.events.map(event => <li key={event.event_id}><b>{event.title}</b><small>{event.event_id} · {event.coverage_status === 'partial_ceiling' ? 'Имеет потолок прироста; влияние зависит от цели' : coverageLabels[event.coverage_status] || event.coverage_status}</small></li>)}</ul> : <p className="empty">В каталоге нет мероприятия, развивающего этот навык.</p>}</div></div></td></tr>}</tbody>)}</table>{!skills.length && <div className="empty">По выбранным фильтрам навыков нет.</div>}</div>
        {skills.length > 10 && <button type="button" className="text-button" onClick={() => setShowAllSkills(value => !value)}>{showAllSkills ? 'Свернуть до 10 навыков' : `Показать все ${skills.length} навыков`}</button>}
      </section></details></>}

      {activeSection === 'next' && <section className="panel"><div className="section-heading"><div><span className="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h2>Кому нужен следующий шаг</h2></div><span className="muted">{gapPeople.length} состояний для проверки · {data.availability.length} сотрудников</span></div><p className="hint">Незапрошенный AI или устаревший подбор не означает, что в каталоге нет подходящих мероприятий. Доступность кандидатов рассчитывается без массовых AI-запросов.</p>
        <div className="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Траектория</th><th>Состояние</th><th>Кандидатов</th><th>Источник подбора</th><th>Обновлено</th></tr></thead><tbody>{data.availability.map(item => <tr key={item.employee_id}><td><Link to={`/hr/employees?id=${encodeURIComponent(item.employee_id)}`}>{item.full_name}</Link><small className="muted block">{item.employee_id}</small></td><td>{item.goal_source ? (goalSourceLabels[item.goal_source] || item.goal_source) : '—'}</td><td>{availabilityLabels[item.state] || item.state}</td><td>{item.candidate_count}</td><td>{item.source === 'ai' ? 'AI' : item.source === 'fallback' ? 'Резервный подбор' : '—'}</td><td>{dateTimeLabel(item.calculated_at)}</td></tr>)}</tbody></table>{!data.availability.length && <div className="empty">Сотрудников по фильтру нет.</div>}</div></section>}

      {activeSection === 'participation' && <><ParticipationSummary items={data.participation} /><section className="panel"><div className="section-heading"><div><span className="eyebrow">УЧАСТИЕ</span><h2>Активности каталога</h2><span className="muted">{visibleParticipation.length} мероприятий</span></div><label className="check-label"><input type="checkbox" checked={includeMandatory} onChange={e => setIncludeMandatory(e.target.checked)} /> Показать обязательные</label></div><p className="hint">Абсолютное число записей выбранного периода. Активные пропуски рекомендаций и архивы HR считаются отдельно от no_show и declined и не ограничиваются периодом.</p>
        <div className="table-wrap"><table><thead><tr><th>Мероприятие</th><th>Завершено</th><th>В работе</th><th>Прекращено</th><th>No-show</th><th>Declined</th><th>Overdue</th><th>Skip</th><th>Архив HR</th></tr></thead><tbody>{visibleParticipation.map(item => <tr key={item.event_id}><td><b>{item.title}</b><small className="muted block">{item.event_id}{item.mandatory ? ' · обязательное' : ''}</small></td><td>{item.completed}</td><td>{item.in_progress}</td><td>{item.dropped}</td><td>{item.no_show}</td><td>{item.declined}</td><td>{item.overdue}</td><td>{item.skips}</td><td>{item.archives}</td></tr>)}</tbody></table>{!visibleParticipation.length && <div className="empty">Мероприятий по фильтру нет.</div>}</div></section></>}
    </>}
  </div>
}
