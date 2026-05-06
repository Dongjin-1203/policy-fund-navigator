'use client'

import type { MatchResponse } from '@/lib/types'
import ProgramCard from './ProgramCard'

interface ProgramListProps {
  data: MatchResponse
}

export default function ProgramList({ data }: ProgramListProps) {
  const programs = data.programs ?? data.ranked_programs ?? []
  const count = data.matched_count ?? programs.length

  if (programs.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-2">
        현재 조건에 맞는 사업을 찾지 못했습니다.
      </div>
    )
  }

  return (
    <div>
      <p className="text-sm font-medium text-gray-700 mb-3">
        총 <span className="text-blue-700 font-bold">{count}건</span>의 정책자금을 추천합니다.
      </p>
      {programs.map((p, i) => (
        <ProgramCard key={p.program_id} program={{ ...p, rank: p.rank ?? i + 1 }} />
      ))}
    </div>
  )
}
