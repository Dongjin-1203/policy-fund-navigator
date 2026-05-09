export interface ProgramItem {
  rank: number
  program_id: string
  program_name: string
  category: string
  score: number
  grade: 'green' | 'yellow' | 'red'
  max_support: number
  interest_rate: string
  apply_end: string
}

export interface ScoreBreakdown {
  F: number
  T: number
  G: number
}

export interface MatchResponse {
  company_id: string
  company_name: string
  dart_found: boolean
  user_input_required: boolean
  required_fields?: string[]
  programs?: ProgramItem[]
  score_breakdown?: ScoreBreakdown
  status?: string
  feedback?: string
  matched_count?: number
  ranked_programs?: ProgramItem[]
}

export interface Contribution {
  alpha_F: number
  beta_T: number
  gamma_G: number
}

export interface TopFeature {
  feature: string
  value: number
  label: string
  name?: string
}

export interface ImprovableFeature {
  feature: string
  current: number
  target: number
  message: string
  name?: string
  label?: string
  delta_pct?: number
}

export interface FeedbackResponse {
  program_id: string
  program_name: string
  score: number
  grade: string
  contribution: Contribution
  top_features: TopFeature[]
  improvable: ImprovableFeature[]
  feedback: string
  score_breakdown?: ScoreBreakdown
}

export interface FinancialData {
  revenue?: number
  debt_ratio?: number
  operating_profit?: number
  cash_flow?: number
  capital?: number
  net_income?: number
  employee_count?: number
  business_age?: number
  is_venture: boolean
  is_innobiz: boolean
  patent_count?: number
  corp_name?: string
}

export type MessageType =
  | 'text'
  | 'programs'
  | 'feedback'
  | 'financial_form'
  | 'loading'

export interface Message {
  id: string
  role: 'bot' | 'user'
  type: MessageType
  content: string
  data?: MatchResponse | FeedbackResponse
  timestamp: Date
}
