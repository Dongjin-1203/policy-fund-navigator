'use client'

import { useState } from 'react'
import { ChevronRight, Loader2 } from 'lucide-react'
import type { ProgramItem, FeedbackResponse } from '@/lib/types'
import { getFeedback } from '@/lib/api'
import { useChatStore } from '@/store/chatStore'
import ScoreBar from './ScoreBar'

interface ProgramCardProps {
  program: ProgramItem
}

const gradeBg: Record<string, string> = {
  green:  'bg-green-50  border-green-500',
  yellow: 'bg-yellow-50 border-yellow-400',
  red:    'bg-red-50    border-red-500',
}

const gradeLabel: Record<string, string> = {
  green:  '🟢 적격',
  yellow: '🟡 조건부',
  red:    '🔴 미달',
}

export default function ProgramCard({ program }: ProgramCardProps) {
  const { company_id, addMessage } = useChatStore()
  const [loading, setLoading] = useState(false)

  const handleFeedback = async () => {
    setLoading(true)
    try {
      const fb: FeedbackResponse = await getFeedback(program.program_id, company_id ?? '')
      addMessage({
        role: 'bot',
        type: 'feedback',
        content: fb.feedback,
        data: fb,
      })
    } catch {
      addMessage({
        role: 'bot',
        type: 'text',
        content: '피드백 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
      })
    } finally {
      setLoading(false)
    }
  }

  const maxSupport = program.max_support
    ? `${(program.max_support / 100_000_000).toFixed(0)}억원`
    : '미정'

  return (
    <div className={`border-l-4 rounded-lg p-4 mb-3 ${gradeBg[program.grade] ?? 'bg-gray-50 border-gray-400'}`}>
      <div className="flex items-start justify-between mb-2">
        <div>
          <span className="text-xs font-medium text-gray-500 mr-2">{gradeLabel[program.grade]}</span>
          <span className="text-xs text-gray-400">{program.category}</span>
        </div>
        <span className="text-xs text-gray-400">#{program.rank}</span>
      </div>

      <p className="font-semibold text-gray-800 mb-3 text-sm leading-snug">{program.program_name}</p>

      <ScoreBar score={program.score} grade={program.grade} />

      <div className="grid grid-cols-3 gap-2 mt-3 text-xs text-gray-600">
        <div>
          <span className="text-gray-400 block">지원한도</span>
          <span className="font-medium">{maxSupport}</span>
        </div>
        <div>
          <span className="text-gray-400 block">금리</span>
          <span className="font-medium">{program.interest_rate || '-'}%</span>
        </div>
        <div>
          <span className="text-gray-400 block">마감</span>
          <span className="font-medium">{program.apply_end || '상시'}</span>
        </div>
      </div>

      <button
        onClick={handleFeedback}
        disabled={loading}
        className="mt-3 w-full flex items-center justify-center gap-1 py-1.5 px-3 rounded-md text-xs font-medium text-blue-700 bg-white border border-blue-200 hover:bg-blue-50 transition-colors disabled:opacity-50"
      >
        {loading ? (
          <Loader2 className="w-3 h-3 animate-spin" />
        ) : (
          <>자세한 분석 보기 <ChevronRight className="w-3 h-3" /></>
        )}
      </button>
    </div>
  )
}
