import type { MatchResponse, ProgramItem } from '@/lib/types'

/**
 * FastAPI MatchResponse → UI용 MatchResponse 정규화.
 * ranked_programs를 programs로 통일하고 grade/rank를 보완한다.
 */
export function normalizeMatchResponse(raw: MatchResponse): MatchResponse {
  const source = raw.programs ?? raw.ranked_programs ?? []

  const programs: ProgramItem[] = source.map((p, i) => ({
    ...p,
    rank: p.rank ?? i + 1,
    score: typeof p.score === 'number' ? parseFloat((p.score * 100).toFixed(1)) : 0,
    grade: p.grade ?? scoreToGrade(p.score),
    interest_rate: p.interest_rate != null ? String(p.interest_rate) : '-',
    apply_end: p.apply_end ?? '',
  }))

  return {
    ...raw,
    user_input_required: raw.user_input_required ?? raw.status === 'user_input_required',
    programs,
  }
}

function scoreToGrade(score: number): 'green' | 'yellow' | 'red' {
  if (score >= 0.6) return 'green'
  if (score >= 0.3) return 'yellow'
  return 'red'
}
