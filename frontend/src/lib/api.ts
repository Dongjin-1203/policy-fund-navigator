import axios from 'axios'
import type { MatchResponse, FeedbackResponse, FinancialData } from '@/lib/types'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const client = axios.create({ baseURL: BASE_URL, timeout: 60000 })

export async function matchCompany(
  company_id: string,
  financial_data?: FinancialData,
): Promise<MatchResponse> {
  const body: Record<string, unknown> = { company_id }

  if (financial_data) {
    const { corp_name, ...fd } = financial_data
    if (corp_name) body.corp_name = corp_name
    body.financial_data = fd
  }

  const { data } = await client.post<MatchResponse>('/api/v1/match', body)
  return data
}

export async function getFeedback(
  program_id: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _company_id: string,
): Promise<FeedbackResponse> {
  const { data } = await client.get<FeedbackResponse>(`/api/v1/feedback/${program_id}`)
  return data
}
