import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api'
import type { Competencies, EmployeeList, ImportPreview } from './types'
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

function readableIssue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const item = value as { field?: string; row?: number; message?: string; detail?: string }
    if (item.message || item.detail) return `${item.row ? `Строка ${item.row}, ` : ''}${item.field ? `${item.field}: ` : ''}${item.message || item.detail}`
  }
  return JSON.stringify(value)
}

function ImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [employeesFile, setEmployeesFile] = useState<File | null>(null)
  const [historyFile, setHistoryFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [confirmUpdates, setConfirmUpdates] = useState(false)
  const [coverSameDayCompletions, setCoverSameDayCompletions] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function showPreview() {
    if (!employeesFile && !historyFile) return
    setBusy(true); setError(''); setPreview(null); setCoverSameDayCompletions(false)
    try {
      const data = new FormData()
      if (employeesFile) data.append('employees_file', employeesFile)
      if (historyFile) data.append('history_file', historyFile)
      setPreview(await api.previewImport(data))
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  async function commit() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      await api.commitImport(preview.token, confirmUpdates, coverSameDayCompletions)
      onImported()
      onClose()
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="modal panel" role="dialog" aria-modal="true" aria-labelledby="import-title">
      <div className="section-heading"><div><span className="eyebrow">ИМПОРТ ДАННЫХ</span><h2 id="import-title">Дополнительные профили и история</h2></div><button className="icon-button" aria-label="Закрыть" onClick={onClose}>×</button></div>
      <p className="hint">Загрузите employees JSON и/или activity_history CSV в исходном формате. Сначала сервер покажет проверку; запись начнётся только после подтверждения.</p>
      <div className="upload-grid"><label className="upload-field"><span>Профили сотрудников · .json</span><input type="file" accept=".json,application/json" onChange={e => { setEmployeesFile(e.target.files?.[0] || null); setPreview(null) }} /><small>{employeesFile?.name || 'Файл не выбран'}</small></label>
        <label className="upload-field"><span>История участия · .csv</span><input type="file" accept=".csv,text/csv" onChange={e => { setHistoryFile(e.target.files?.[0] || null); setPreview(null) }} /><small>{historyFile?.name || 'Файл не выбран'}</small></label></div>
      {error && <div className="alert error" role="alert">{error}</div>}
      {preview && <div className="preview-box">
        <h3>Предварительная проверка</h3><div className="preview-stats"><span><b>{preview.new_employees}</b> новых сотрудников</span><span><b>{preview.updated_employees}</b> обновлений профиля</span><span><b>{preview.new_history}</b> новых записей истории</span><span><b>{preview.duplicates}</b> точных дублей</span></div>
        {preview.errors.length > 0 ? <div className="alert error"><b>Ошибки ({preview.errors.length})</b><ul>{preview.errors.map((item, index) => <li key={index}>{readableIssue(item)}</li>)}</ul></div> : <div className="alert success">Проверка пройдена. Импорт будет выполнен одной транзакцией.</div>}
        {!!preview.warnings?.length && <div className="alert warning"><b>Предупреждения ({preview.warnings.length})</b><ul>{preview.warnings.map((item, index) => <li key={index}>{readableIssue(item)}</li>)}</ul></div>}
        {preview.updated_employees > 0 && <label className="check-label"><input type="checkbox" checked={confirmUpdates} onChange={e => setConfirmUpdates(e.target.checked)} /> Подтверждаю обновление {preview.updated_employees} существующих профилей</label>}
        {!!preview.same_day_completions?.length && <div className="same-day-box"><strong>Завершения в день новой оценки: {preview.same_day_completions.length}</strong><p className="hint">Укажите, учтены ли эти завершения в загружаемых уровнях навыков. Если нет, приложение сохранит их прирост при пересчёте.</p><ul>{preview.same_day_completions.map(item => <li key={item.id}>{item.employee_id} · {item.event_id} · {item.completion_date} <small>{item.id}</small></li>)}</ul><label className="check-label"><input type="checkbox" checked={coverSameDayCompletions} onChange={e => setCoverSameDayCompletions(e.target.checked)} /> Новая оценка уже включает эти завершения в день оценки</label></div>}
      </div>}
      <div className="modal-actions"><button className="button subtle" onClick={onClose}>Отмена</button>{!preview ? <button className="button primary" disabled={busy || (!employeesFile && !historyFile)} onClick={() => void showPreview()}>{busy ? 'Проверяем…' : 'Проверить файлы'}</button> : <button className="button primary" disabled={busy || preview.ok === false || preview.errors.length > 0 || (preview.updated_employees > 0 && !confirmUpdates)} onClick={() => void commit()}>{busy ? 'Импортируем…' : 'Подтвердить импорт'}</button>}</div>
    </div>
  </div>
}

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
  const [openImport, setOpenImport] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
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
    if (includeMandatory) params.set('mandatory', 'all')
    try { setData(await api.competencies(params)) }
    catch (err) { setError(errorText(err)) }
    finally { setLoading(false) }
  }, [department, role, grade, type, dateFrom, dateTo, includeMandatory])

  useEffect(() => { void load() }, [load, revision])
  useEffect(() => { api.employees(new URLSearchParams()).then(setOptions).catch(() => {}) }, [revision])
  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === 'visible') setRevision(value => value + 1) }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  const skills = useMemo(() => [...(data?.skills ?? [])].sort((a, b) => b.gap_count - a.gap_count || b.critical_count - a.critical_count), [data])
  const visibleParticipation = (data?.participation ?? []).filter(item => includeMandatory || !item.mandatory)
  const gapPeople = data?.availability.filter(item => item.state !== 'recommended' && item.state !== 'active' && item.state !== 'available') ?? []
  const gapTotal = data?.skills.reduce((sum, item) => sum + item.gap_count, 0) ?? 0

  return <div className="competencies-page">
    <section className="page-title"><div><span className="eyebrow">HR · АНАЛИТИКА</span><h1>Компетенции и участие</h1><p>Разрывы сравниваются с индивидуальной целью каждого сотрудника. Данные меняются после выполнения активности и импорта.</p></div><button className="button primary" onClick={() => setOpenImport(true)}>Импорт данных</button></section>
    {error && <div className="alert error" role="alert">{error} <button className="text-button" onClick={() => void load()}>Повторить</button></div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    <section className="panel filter-panel"><div className="section-heading"><h2>Фильтры</h2><button className="text-button" onClick={() => { setDepartment(''); setRole(''); setGrade(''); setType(''); setDateFrom(''); setDateTo('') }}>Сбросить</button></div>
      <div className="filters"><label>Отдел<select value={department} onChange={e => setDepartment(e.target.value)}><option value="">Все отделы</option>{options?.departments.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Текущая роль<select value={role} onChange={e => setRole(e.target.value)}><option value="">Все роли</option>{options?.roles.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Грейд<select value={grade} onChange={e => setGrade(e.target.value)}><option value="">Все грейды</option>{options?.grades.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Тип навыка<select value={type} onChange={e => setType(e.target.value)}><option value="">Hard и soft</option><option value="hard">Hard</option><option value="soft">Soft</option></select></label>
        <label>Период с<input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label><label>Период по<input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} /></label></div>
      <p className="hint">Период применяется к участию по активностям; разрывы навыков считаются на дату среза.</p>
    </section>

    {loading && !data ? <div className="panel loading-panel">Считаем показатели по текущим данным…</div> : data && <>
      <div className="stats-grid"><div className="panel metric"><span>Навыки с разрывами</span><strong>{skills.filter(s => s.gap_count > 0).length}</strong><small>Из {skills.length} навыков выбранной группы</small></div><div className="panel metric"><span>Разрывы у сотрудников</span><strong>{gapTotal}</strong><small>Сумма пар «сотрудник — навык»</small></div><div className="panel metric"><span>Состояния для проверки</span><strong>{gapPeople.length}</strong><small>Смотрите точную причину ниже</small></div></div>
      <section className="panel"><div className="section-heading"><div><span className="eyebrow">А · РАЗРЫВЫ</span><h2>Где требуется развитие</h2></div><span className="muted">{loading ? 'Обновляем…' : `${skills.length} навыков`}</span></div>
        <p className="hint">Доля = сотрудники с разрывом / сотрудники, для чьей цели требуется навык. Нажмите на строку для подробностей.</p>
        <div className="table-wrap"><table><thead><tr><th>Навык</th><th>Тип</th><th>Требуется людям</th><th>С разрывом</th><th>Доля</th><th>Критических</th></tr></thead>{skills.map(skill => <tbody key={skill.skill_id}><tr className="clickable-row" onClick={() => setExpanded(expanded === skill.skill_id ? '' : skill.skill_id)}><td><b>{skill.name}</b><small className="muted block">{skill.skill_id}</small></td><td>{skill.type === 'hard' ? 'Hard' : 'Soft'}</td><td>{skill.required_count}</td><td><b className={skill.gap_count ? 'gap' : 'ok'}>{skill.gap_count}</b></td><td>{skill.gap_pct == null ? '—' : `${numberLabel(skill.gap_pct, 1)}%`}</td><td>{skill.critical_count}</td></tr>{expanded === skill.skill_id && <tr className="expanded-row"><td colSpan={6}><div className="skill-detail"><div><h3>Затронутые сотрудники</h3>{skill.people.length ? <div className="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Сейчас</th><th>Цель</th><th>Разрыв</th><th>Каталог</th></tr></thead><tbody>{skill.people.map(person => <tr key={person.employee_id}><td><Link to={`/hr/employees?id=${encodeURIComponent(person.employee_id)}`}>{person.full_name}</Link><small className="muted block">{person.employee_id}</small></td><td>{person.current}</td><td>{person.required}</td><td>{person.gap}</td><td>{coverageLabels[person.coverage_status] || person.coverage_status}</td></tr>)}</tbody></table></div> : <p className="empty">Нет сотрудников с разрывом по фильтру.</p>}</div><div><h3>Мероприятия каталога</h3>{skill.events.length ? <ul className="event-list">{skill.events.map(event => <li key={event.event_id}><b>{event.title}</b><small>{event.event_id} · {event.coverage_status === 'partial_ceiling' ? 'Имеет потолок прироста; влияние зависит от цели' : coverageLabels[event.coverage_status] || event.coverage_status}</small></li>)}</ul> : <p className="empty">В каталоге нет мероприятия, развивающего этот навык.</p>}</div></div></td></tr>}</tbody>)}</table>{!skills.length && <div className="empty">По выбранным фильтрам навыков нет.</div>}</div>
      </section>

      <section className="panel"><div className="section-heading"><div><span className="eyebrow">Б · ДОСТУПНОСТЬ</span><h2>Кому нужен следующий шаг</h2></div><span className="muted">{data.availability.length} сотрудников</span></div><p className="hint">Незапрошенный AI или устаревший подбор не означает, что в каталоге нет подходящих мероприятий. Доступность кандидатов рассчитывается без массовых AI-запросов.</p>
        <div className="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Траектория</th><th>Состояние</th><th>Кандидатов</th><th>Источник подбора</th><th>Обновлено</th></tr></thead><tbody>{data.availability.map(item => <tr key={item.employee_id}><td><Link to={`/hr/employees?id=${encodeURIComponent(item.employee_id)}`}>{item.full_name}</Link><small className="muted block">{item.employee_id}</small></td><td>{item.goal_source ? (goalSourceLabels[item.goal_source] || item.goal_source) : '—'}</td><td>{availabilityLabels[item.state] || item.state}</td><td>{item.candidate_count}</td><td>{item.source === 'ai' ? 'AI' : item.source === 'fallback' ? 'Резервный подбор' : '—'}</td><td>{dateTimeLabel(item.calculated_at)}</td></tr>)}</tbody></table>{!data.availability.length && <div className="empty">Сотрудников по фильтру нет.</div>}</div></section>

      <section className="panel"><div className="section-heading"><div><span className="eyebrow">В · УЧАСТИЕ</span><h2>Активности каталога</h2></div><label className="check-label"><input type="checkbox" checked={includeMandatory} onChange={e => setIncludeMandatory(e.target.checked)} /> Показать обязательные</label></div><p className="hint">Абсолютное число записей выбранного периода. Активные пропуски рекомендаций и архивы HR считаются отдельно от no_show и declined и не ограничиваются периодом.</p>
        <div className="table-wrap"><table><thead><tr><th>Мероприятие</th><th>Завершено</th><th>В работе</th><th>Прекращено</th><th>No-show</th><th>Declined</th><th>Overdue</th><th>Skip</th><th>Архив HR</th></tr></thead><tbody>{visibleParticipation.map(item => <tr key={item.event_id}><td><b>{item.title}</b><small className="muted block">{item.event_id}{item.mandatory ? ' · обязательное' : ''}</small></td><td>{item.completed}</td><td>{item.in_progress}</td><td>{item.dropped}</td><td>{item.no_show}</td><td>{item.declined}</td><td>{item.overdue}</td><td>{item.skips}</td><td>{item.archives}</td></tr>)}</tbody></table>{!visibleParticipation.length && <div className="empty">Мероприятий по фильтру нет.</div>}</div></section>
    </>}
    {openImport && <ImportModal onClose={() => setOpenImport(false)} onImported={() => { setNotice('Импорт завершён. Показатели обновлены.'); setRevision(value => value + 1) }} />}
  </div>
}
