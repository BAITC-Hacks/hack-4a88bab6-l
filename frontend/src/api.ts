import type { Competencies, EmployeeList, ImportPreview, Profile, RecSet, User } from './types'

let csrfToken = ''

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

function readableError(body: unknown, fallback: string): string {
  if (typeof body === 'string') return body
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((item) => {
      if (item && typeof item === 'object') {
        const row = item as { loc?: unknown[]; msg?: string }
        return `${row.loc?.join('.') ?? 'Поле'}: ${row.msg ?? 'неверное значение'}`
      }
      return String(item)
    }).join('; ')
  }
  return fallback
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const isForm = options.body instanceof FormData
  const method = (options.method || 'GET').toUpperCase()
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      credentials: 'same-origin',
      ...options,
      headers: {
        ...(isForm ? {} : options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(method !== 'GET' && path !== '/login' && csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
        ...options.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'Не удалось связаться с сервером. Проверьте подключение и повторите запрос.')
  }
  if (response.status === 204) return undefined as T
  const raw = await response.text()
  let body: unknown = null
  try { body = raw ? JSON.parse(raw) : null } catch { body = raw }
  if (response.status === 401 && path !== '/me' && path !== '/login') window.dispatchEvent(new Event('cq:unauthorized'))
  if (!response.ok) throw new ApiError(response.status, readableError(body, `Ошибка сервера (${response.status})`))
  return body as T
}

export const api = {
  me: async () => {
    const user = await request<User>('/me')
    csrfToken = user.csrf_token || ''
    return user
  },
  login: (email: string, password: string) => request<User>('/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: async () => { await request<void>('/logout', { method: 'POST' }); csrfToken = '' },
  roleProfiles: () => request<{ items: { role: string; grade: string }[] }>('/role-profiles'),
  employeeProfile: () => request<Profile>('/employee/profile'),
  setGoal: (role: string, grade: string) => request<Profile>('/employee/goal', { method: 'POST', body: JSON.stringify({ role, grade }) }),
  employeeRecommendations: () => request<RecSet>('/employee/recommendations'),
  generateEmployeeRecommendations: () => request<RecSet>('/employee/recommendations/generate', { method: 'POST' }),
  skip: (eventId: string, reason?: string) => request<void>(`/employee/recommendations/${encodeURIComponent(eventId)}/skip`, { method: 'POST', body: JSON.stringify({ reason: reason || null }) }),
  restoreSkip: (eventId: string) => request<void>(`/employee/recommendations/${encodeURIComponent(eventId)}/skip`, { method: 'DELETE' }),
  start: (eventId: string, sessionDate?: string | null) => request<void>('/employee/participations', { method: 'POST', body: JSON.stringify({ event_id: eventId, session_date: sessionDate || null }) }),
  complete: (id: string, simulate: boolean) => request<void>(`/employee/participations/${encodeURIComponent(id)}/complete`, { method: 'POST', body: JSON.stringify({ simulate }) }),
  stop: (id: string) => request<void>(`/employee/participations/${encodeURIComponent(id)}/stop`, { method: 'POST', body: '{}' }),
  employees: (params: URLSearchParams) => request<EmployeeList>(`/hr/employees?${params}`),
  hrProfile: (id: string) => request<Profile>(`/hr/employees/${encodeURIComponent(id)}`),
  hrRecommendations: (id: string) => request<RecSet>(`/hr/employees/${encodeURIComponent(id)}/recommendations`),
  generateHrRecommendations: (id: string) => request<RecSet>(`/hr/employees/${encodeURIComponent(id)}/recommendations/generate`, { method: 'POST' }),
  archive: (id: string, eventId: string, reason: string) => request<void>(`/hr/employees/${encodeURIComponent(id)}/archive/${encodeURIComponent(eventId)}`, { method: 'POST', body: JSON.stringify({ reason }) }),
  restoreArchive: (id: string, eventId: string) => request<void>(`/hr/employees/${encodeURIComponent(id)}/archive/${encodeURIComponent(eventId)}`, { method: 'DELETE' }),
  competencies: (params: URLSearchParams) => request<Competencies>(`/hr/competencies?${params}`),
  previewImport: (files: FormData) => request<ImportPreview>('/hr/import/preview', { method: 'POST', body: files }),
  commitImport: (token: string, confirmUpdates: boolean, coverSameDayCompletions: boolean) => request<unknown>('/hr/import/commit', { method: 'POST', body: JSON.stringify({ token, confirm_updates: confirmUpdates, cover_same_day_completions: coverSameDayCompletions }) }),
}
