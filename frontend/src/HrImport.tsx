import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api'
import type { ImportPreview } from './types'
import { errorText } from './ui'

type ImportResult = {
  affected_employees?: string[]
  covered_same_day_completions?: number
  demo_accounts_created?: number
}

function readableIssue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const item = value as { field?: string; row?: number; message?: string; detail?: string }
    if (item.message || item.detail) {
      return `${item.row ? `Строка ${item.row}, ` : ''}${item.field ? `${item.field}: ` : ''}${item.message || item.detail}`
    }
  }
  return JSON.stringify(value)
}

function ImportForm() {
  const [employeesFile, setEmployeesFile] = useState<File | null>(null)
  const [historyFile, setHistoryFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [confirmUpdates, setConfirmUpdates] = useState(false)
  const [coverSameDayCompletions, setCoverSameDayCompletions] = useState(false)
  const [outcome, setOutcome] = useState<{ preview: ImportPreview; result: ImportResult } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function invalidatePreview() {
    setPreview(null)
    setConfirmUpdates(false)
    setCoverSameDayCompletions(false)
    setError('')
  }

  async function showPreview() {
    if (!employeesFile && !historyFile) return
    setBusy(true)
    setError('')
    setPreview(null)
    setConfirmUpdates(false)
    setCoverSameDayCompletions(false)
    try {
      const files = new FormData()
      if (employeesFile) files.append('employees_file', employeesFile)
      if (historyFile) files.append('history_file', historyFile)
      setPreview(await api.previewImport(files))
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  async function commit() {
    if (!preview || preview.ok === false || preview.errors.length) return
    setBusy(true)
    setError('')
    try {
      const result = await api.commitImport(preview.token, confirmUpdates, coverSameDayCompletions) as ImportResult
      setOutcome({ preview, result })
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  function startAgain() {
    setEmployeesFile(null)
    setHistoryFile(null)
    setPreview(null)
    setConfirmUpdates(false)
    setCoverSameDayCompletions(false)
    setOutcome(null)
    setError('')
  }

  if (outcome) {
    const firstEmployee = outcome.result.affected_employees?.[0]
    const employeesUrl = firstEmployee ? `/hr/employees?id=${encodeURIComponent(firstEmployee)}` : '/hr/employees'
    return <section className="panel import-result" role="status">
      <span className="eyebrow">ИМПОРТ ЗАВЕРШЁН</span>
      <h2>Данные сохранены</h2>
      <p>Профили и история доступны в HR-разделах. Рекомендации и сводка пересчитываются по обновлённым данным.</p>
      <div className="preview-stats">
        <span><b>{outcome.preview.new_employees}</b> новых сотрудников</span>
        <span><b>{outcome.preview.updated_employees}</b> обновлений профиля</span>
        <span><b>{outcome.preview.new_history}</b> новых записей истории</span>
        <span><b>{outcome.preview.duplicates}</b> точных дублей без повторной записи</span>
      </div>
      {typeof outcome.result.covered_same_day_completions === 'number' && outcome.result.covered_same_day_completions > 0 && <p className="hint">Завершений, уже учтённых новой оценкой: {outcome.result.covered_same_day_completions}.</p>}
      <div className="import-actions"><Link className="button primary" to={employeesUrl}>Открыть сотрудников</Link><Link className="button subtle" to="/hr/competencies">Открыть компетенции</Link><button className="button subtle" onClick={startAgain}>Импортировать ещё</button></div>
    </section>
  }

  return <section className="panel import-form">
    <div className="section-heading"><div><span className="eyebrow">ШАГ 1</span><h2>Выберите файлы</h2></div></div>
    <p className="hint">Можно выбрать JSON, CSV или оба файла. Повторно загружать стартовые 200 профилей и весь каталог не требуется.</p>
    <div className="upload-grid import-upload-grid">
      <label className="upload-field"><span>Профили сотрудников · employees.json</span><input type="file" disabled={busy} accept=".json,application/json" onChange={event => { setEmployeesFile(event.target.files?.[0] || null); invalidatePreview() }} /><small>{employeesFile?.name || 'Файл не выбран · необязательно'}</small></label>
      <label className="upload-field"><span>История участия · activity_history.csv</span><input type="file" disabled={busy} accept=".csv,text/csv" onChange={event => { setHistoryFile(event.target.files?.[0] || null); invalidatePreview() }} /><small>{historyFile?.name || 'Файл не выбран · необязательно'}</small></label>
    </div>
    <div className="import-actions"><button className="button primary" disabled={busy || (!employeesFile && !historyFile)} onClick={() => void showPreview()}>{busy && !preview ? 'Проверяем…' : preview ? 'Проверить заново' : 'Проверить файлы'}</button></div>

    {error && <div className="alert error" role="alert">{error}</div>}
    {preview && <div className="preview-box import-preview">
      <span className="eyebrow">ШАГ 2</span><h2>Предварительная проверка</h2>
      <p className="hint">Просмотрите изменения до записи. При конфликте ID с другим содержимым импорт не начнётся.</p>
      <div className="preview-stats"><span><b>{preview.new_employees}</b> новых сотрудников</span><span><b>{preview.updated_employees}</b> обновлений профиля</span><span><b>{preview.new_history}</b> новых записей истории</span><span><b>{preview.duplicates}</b> точных дублей</span></div>
      {preview.errors.length > 0 ? <div className="alert error"><b>Ошибки ({preview.errors.length})</b><ul>{preview.errors.map((item, index) => <li key={index}>{readableIssue(item)}</li>)}</ul></div> : <div className="alert success">Проверка пройдена. Импорт будет выполнен одной транзакцией после подтверждения.</div>}
      {!!preview.warnings?.length && <div className="alert warning"><b>Предупреждения ({preview.warnings.length})</b><ul>{preview.warnings.map((item, index) => <li key={index}>{readableIssue(item)}</li>)}</ul></div>}
      {preview.updated_employees > 0 && <label className="check-label"><input type="checkbox" checked={confirmUpdates} onChange={event => setConfirmUpdates(event.target.checked)} /> Подтверждаю обновление {preview.updated_employees} существующих профилей</label>}
      {!!preview.same_day_completions?.length && <div className="same-day-box"><strong>Завершения в день новой оценки: {preview.same_day_completions.length}</strong><p className="hint">Укажите, учтены ли эти завершения в загружаемых уровнях навыков. Если нет, приложение сохранит их прирост при пересчёте.</p><ul>{preview.same_day_completions.map(item => <li key={item.id}>{item.employee_id} · {item.event_id} · {item.completion_date} <small>{item.id}</small></li>)}</ul><label className="check-label"><input type="checkbox" checked={coverSameDayCompletions} onChange={event => setCoverSameDayCompletions(event.target.checked)} /> Новая оценка уже включает эти завершения в день оценки</label></div>}
      <div className="import-confirm"><span className="eyebrow">ШАГ 3</span><h2>Подтвердите импорт</h2><p className="hint">После подтверждения изменения сохранятся. Неисправленные ошибки и неподтверждённые обновления блокируют запись.</p><button className="button primary" disabled={busy || preview.ok === false || preview.errors.length > 0 || (preview.updated_employees > 0 && !confirmUpdates)} onClick={() => void commit()}>{busy ? 'Импортируем…' : 'Подтвердить импорт'}</button></div>
    </div>}
  </section>
}

export default function HrImport() {
  return <div className="hr-import-page">
    <section className="page-title"><div><span className="eyebrow">HR · ИМПОРТ</span><h1>Импорт профилей и истории</h1><p>Загрузите дополнительные данные в исходном формате датасета, проверьте изменения и подтвердите сохранение.</p></div><Link className="button subtle" to="/hr/competencies">К компетенциям</Link></section>
    <section className="panel import-guide"><h2>Поддерживаемые форматы</h2><div className="import-guide-grid"><div><strong>employees.json</strong><p>JSON объект с полями <code>meta</code> и <code>employees</code>. Подходят дополнительные профили и обновления существующих сотрудников.</p></div><div><strong>activity_history.csv</strong><p>CSV с исходными колонками <code>record_id, employee_id, event_id, date, due_date, status, completion_pct, score, feedback_rating, assigned_by</code>.</p></div></div><p className="hint">ID сотрудников и мероприятий проверяются по уже загруженным данным. Точные дубли не записываются повторно; ошибки показываются до подтверждения.</p></section>
    <ImportForm />
  </div>
}
