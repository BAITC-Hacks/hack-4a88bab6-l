export const statusLabels: Record<string, string> = {
  completed: 'Завершено',
  in_progress: 'В работе',
  planned: 'Запланировано',
  dropped: 'Прекращено',
  no_show: 'Не явился на сессию',
  declined: 'Отказ от назначения',
  overdue: 'Просрочено',
  stopped: 'Прекращено',
}

export const formatLabels: Record<string, string> = {
  online: 'Онлайн',
  offline: 'Очно',
  self_paced: 'В своём темпе',
}

export const goalSourceLabels: Record<string, string> = {
  explicit: 'Подтверждённая цель',
  suggested_next_grade: 'Предлагаемая траектория, цель пока не подтверждена',
  current_role_development: 'Цель не задана — развитие в текущей роли',
}

export const availabilityLabels: Record<string, string> = {
  available: 'Есть доступный шаг',
  recommended: 'Есть рекомендация',
  active: 'Есть рекомендация',
  no_candidates: 'Нет подходящих кандидатов',
  skipped: 'Предложения пропущены сотрудником',
  archived: 'Предложения убраны HR',
  suggested_goal: 'Используется предложенная цель',
  no_goal: 'Цель не задана',
  covered: 'Требования цели уже покрыты',
  requirements_covered: 'Требования цели уже покрыты',
  stale: 'Рекомендации устарели',
  not_requested: 'Рекомендации ещё не запрошены',
  not_requested_or_stale: 'Рекомендации ещё не запрошены или устарели',
  no_eligible_candidates: 'Нет подходящих кандидатов',
  ai_error: 'Ошибка AI',
  fallback: 'Резервный подбор',
}

export function dateLabel(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(`${value.slice(0, 10)}T12:00:00`)
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }).format(d)
}

export function dateTimeLabel(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(d)
}

export function numberLabel(value: number | null | undefined, digits = 0): string {
  return value == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value)
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Неизвестная ошибка. Повторите действие.'
}
