'use client'

import type { FeedbackResponse } from '@/lib/types'
import ContributionChart from './ContributionChart'
import ImprovableList from './ImprovableList'

interface FeedbackPanelProps {
  data: FeedbackResponse
}

export default function FeedbackPanel({ data }: FeedbackPanelProps) {
  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-semibold text-gray-700 text-base">{data.program_name}</p>
      </div>

      {data.contribution && (
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">점수 기여도</p>
          <ContributionChart contribution={data.contribution} />
        </div>
      )}

      {data.top_features && data.top_features.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">주요 강점</p>
          <ul className="space-y-1">
            {data.top_features.map((f) => (
              <li key={f.name ?? f.feature} className="flex items-center gap-2 text-xs text-gray-600">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 shrink-0" />
                <span>{f.label ?? f.name ?? f.feature}</span>
                <span className="ml-auto font-medium text-blue-600">
                  {(f.value * 100).toFixed(1)}점
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.improvable && (
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">보완 가능 항목</p>
          <ImprovableList items={data.improvable} />
        </div>
      )}

      {data.feedback && (
        <div className="bg-blue-50 rounded-lg p-3">
          <p className="text-xs font-semibold text-blue-700 mb-1">AI 개선 가이드</p>
          <p className="text-xs text-gray-700 leading-relaxed">{data.feedback}</p>
        </div>
      )}
    </div>
  )
}
