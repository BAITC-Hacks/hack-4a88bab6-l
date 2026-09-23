import { useMemo, useState } from 'react'
import type { Competencies } from './types'
import { numberLabel } from './ui'

const pct = (value: number | null | undefined) => value == null ? '—' : `${numberLabel(value, 1)}%`
const bounded = (value: number | null | undefined) => Math.max(0, Math.min(100, value ?? 0))

export function GapBars({ skills, onSelect }: { skills: Competencies['skills']; onSelect: (skillId: string) => void }) {
  const top = useMemo(() => [...skills].filter(skill => skill.gap_count > 0)
    .sort((a, b) => b.gap_count - a.gap_count || b.critical_count - a.critical_count || a.name.localeCompare(b.name, 'ru'))
    .slice(0, 8), [skills])
  const max = Math.max(1, ...top.map(skill => skill.gap_count))

  return <section className="panel hr-chart-card hr-gap-bars">
    <div className="section-heading"><div><span className="eyebrow">РАЗРЫВЫ</span><h2>Где развитие нужнее всего</h2></div><span className="muted">Топ 8 навыков</span></div>
    <p className="hint">Длина полосы сравнивает число сотрудников с разрывом. Доля считается только среди тех, кому навык требуется.</p>
    {top.length ? <div className="hr-gap-bars-list">{top.map(skill => <button type="button" className="hr-gap-bar-row" key={skill.skill_id} onClick={() => onSelect(skill.skill_id)} title={`${skill.name}: ${skill.gap_count} из ${skill.required_count} сотрудников с разрывом; доля ${pct(skill.gap_pct)}; критических разрывов ${skill.critical_count}`} aria-label={`Показать детали навыка ${skill.name}: ${skill.gap_count} из ${skill.required_count} с разрывом`}>
      <span className="hr-gap-bar-name">{skill.name}</span>
      <span className="hr-gap-bar-track" aria-hidden="true"><span className="hr-gap-bar-fill" style={{ width: `${100 * skill.gap_count / max}%` }} /></span>
      <strong>{skill.gap_count}<small> / {skill.required_count}</small></strong>
    </button>)}</div> : <div className="empty">По выбранным фильтрам разрывов нет.</div>}
  </section>
}

export function DepartmentHeatmap({ cells, skills, onSelect }: {
  cells: Competencies['department_skills']
  skills: Competencies['skills']
  onSelect: (skillId: string, department: string) => void
}) {
  const top = useMemo(() => [...skills].filter(skill => skill.gap_count > 0)
    .sort((a, b) => b.gap_count - a.gap_count || b.critical_count - a.critical_count || a.name.localeCompare(b.name, 'ru'))
    .slice(0, 8), [skills])
  const departments = useMemo(() => [...new Set(cells.map(cell => cell.department))].sort((a, b) => a.localeCompare(b, 'ru')), [cells])
  const cellMap = useMemo(() => new Map(cells.map(cell => [`${cell.skill_id}\u0000${cell.department}`, cell])), [cells])

  return <section className="panel hr-chart-card hr-department-heatmap">
    <div className="section-heading"><div><span className="eyebrow">ПО ОТДЕЛАМ</span><h2>Карта разрывов</h2></div></div>
    <p className="hint">Доля сотрудников с разрывом среди тех, кому навык нужен в данном отделе. «—» — навык не требуется.</p>
    {top.length && departments.length ? <div className="table-wrap hr-heatmap-scroll"><table><thead><tr><th scope="col">Навык</th>{departments.map(department => <th scope="col" key={department}>{department}</th>)}</tr></thead><tbody>{top.map(skill => <tr key={skill.skill_id}><th scope="row">{skill.name}</th>{departments.map(department => {
      const cell = cellMap.get(`${skill.skill_id}\u0000${department}`)
      if (!cell || !cell.required_count) return <td className="hr-heatmap-na" key={department}>—</td>
      const share = bounded(cell.gap_pct)
      const tooltip = `${skill.name} · ${department}: с разрывом ${cell.gap_count} из ${cell.required_count}; доля ${pct(cell.gap_pct)}; критических ${cell.critical_count}`
      return <td key={department}><button type="button" className="hr-heatmap-cell" style={{ backgroundColor: `rgba(111, 99, 160, ${0.08 + share / 100 * 0.7})`, color: share > 58 ? '#fff' : '#31304b' }} title={tooltip} aria-label={`${tooltip}. Показать сотрудников.`} onClick={() => onSelect(skill.skill_id, department)}>{pct(cell.gap_pct)}</button></td>
    })}</tr>)}</tbody></table></div> : <div className="empty">Для карты по выбранным фильтрам данных нет.</div>}
  </section>
}

const statusMeta = [
  { key: 'completed', label: 'Завершено' },
  { key: 'in_progress', label: 'В работе' },
  { key: 'dropped', label: 'Прекращено' },
  { key: 'no_show', label: 'Неявка' },
  { key: 'declined', label: 'Отказ' },
  { key: 'overdue', label: 'Просрочено' },
] as const

export function ParticipationSummary({ items }: { items: Competencies['participation'] }) {
  const [group, setGroup] = useState<'voluntary' | 'mandatory'>('voluntary')
  const selected = items.filter(item => item.mandatory === (group === 'mandatory'))
  const counts = Object.fromEntries(statusMeta.map(status => [status.key, selected.reduce((sum, item) => sum + item[status.key], 0)])) as Record<(typeof statusMeta)[number]['key'], number>
  const total = statusMeta.reduce((sum, status) => sum + counts[status.key], 0)
  const outcomes = counts.completed + counts.dropped + counts.no_show + counts.declined
  const completionRate = outcomes ? 100 * counts.completed / outcomes : null

  return <section className="panel hr-chart-card hr-participation-summary">
    <div className="section-heading"><div><span className="eyebrow">УЧАСТИЕ</span><h2>Как завершаются активности</h2></div><div className="hr-participation-switch" role="group" aria-label="Тип активностей"><button type="button" className={group === 'voluntary' ? 'active' : ''} aria-pressed={group === 'voluntary'} onClick={() => setGroup('voluntary')}>Добровольные</button><button type="button" className={group === 'mandatory' ? 'active' : ''} aria-pressed={group === 'mandatory'} onClick={() => setGroup('mandatory')}>Обязательные</button></div></div>
    <div className="hr-participation-main"><div><strong>{pct(completionRate)}</strong><span>доля завершений по исходам</span></div><p>{counts.completed} завершено из {outcomes} завершённых исходов</p></div>
    {total ? <div className="hr-participation-stack" role="img" aria-label={statusMeta.map(status => `${status.label}: ${counts[status.key]}`).join(', ')}>{statusMeta.filter(status => counts[status.key] > 0).map(status => <span key={status.key} className={`hr-participation-segment ${status.key}`} style={{ width: `${100 * counts[status.key] / total}%` }} title={`${status.label}: ${counts[status.key]}`} />)}</div> : <div className="empty">Записей участия по выбранным фильтрам нет.</div>}
    <div className="hr-participation-legend">{statusMeta.map(status => <span key={status.key}><i className={`hr-participation-key ${status.key}`} />{status.label}: <b>{counts[status.key]}</b></span>)}</div>
    <p className="hint">Считаются записи участия, включая повторы. Для доли завершений знаменатель: завершено + прекращено + неявка + отказ. В работе и просрочено показаны на шкале, но в знаменатель не входят. Skip и архив рекомендаций считаются отдельно.</p>
  </section>
}
