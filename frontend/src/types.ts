export type User = {
  role: 'employee' | 'hr'
  employee_id: string | null
  demo_mode: boolean
  as_of_date: string
  csrf_token?: string
}

export type Skill = {
  skill_id: string
  name: string
  type: 'hard' | 'soft'
  current: number
  required: number
  gap: number
  critical: boolean
}

export type History = {
  id: string
  event_id: string
  title: string
  date: string
  status: string
  completion_pct: number
  source: string
  is_simulated: boolean
  mandatory: boolean
  linked_participation_id?: string | null
  date_meaning?: string
}

export type Participation = {
  id: string
  event_id: string
  title: string
  status: string
  session_date: string | null
  is_simulated: boolean
  linked_record_id?: string | null
}

export type Recommendation = {
  event_id: string
  title: string
  type: string
  format: string
  duration_hours: number
  session_date: string | null
  action: 'start' | 'continue'
  participation_status?: 'planned' | 'in_progress' | null
  participation_id?: string | null
  develops: { skill_id: string; name: string; current: number; after: number; required: number }[]
  factors: { id: string; label: string; source: string }[]
  rationale: string
  tradeoff?: string | null
}

export type RecSet = {
  source: 'ai' | 'fallback' | null
  version: string | number | null
  calculated_at: string | null
  status: string
  items: Recommendation[]
}

export type Profile = {
  employee: {
    employee_id: string
    full_name: string
    department: string
    role: string
    grade: string
    preferred_language: string
    last_review_date: string
  }
  as_of_date: string
  demo_mode: boolean
  goal: { role: string; grade: string; source: string }
  coverage_pct: number | null
  critical_gaps: number
  skills: Skill[]
  dynamics: { date: string; skills: Record<string, number> }[]
  history: History[]
  participations: Participation[]
  skips: { event_id: string; title?: string; reason?: string | null }[]
  archives: { event_id: string; title?: string; reason: string }[]
  recommendations: RecSet
  availability?: { state: string; candidate_count: number; goal_source: string }
  exclusions?: Record<string, number>
  activity_calendar?: {
    from: string
    to: string
    total: number
    days: { date: string; count: number; estimated_count: number; simulated_count: number }[]
  }
}

export type EmployeeList = {
  items: { employee_id: string; full_name: string; department: string; role: string; grade: string }[]
  departments: string[]
  roles: string[]
  grades: string[]
}

export type Competencies = {
  skills: {
    skill_id: string
    name: string
    type: 'hard' | 'soft'
    required_count: number
    gap_count: number
    gap_pct: number | null
    critical_count: number
    people: { employee_id: string; full_name: string; current: number; required: number; gap: number; coverage_status: string }[]
    events: { event_id: string; title: string; coverage_status: string }[]
  }[]
  availability: {
    employee_id: string
    full_name: string
    state: string
    candidate_count: number
    goal_source?: string
    source: string | null
    calculated_at: string | null
  }[]
  participation: {
    event_id: string
    title: string
    mandatory: boolean
    completed: number
    in_progress: number
    dropped: number
    no_show: number
    declined: number
    overdue: number
    skips: number
    archives: number
  }[]
}

export type ImportPreview = {
  token: string
  new_employees: number
  updated_employees: number
  new_history: number
  duplicates: number
  errors: unknown[]
  warnings?: unknown[]
  ok?: boolean
  same_day_completions?: { id: string; employee_id: string; event_id: string; completion_date: string }[]
}
