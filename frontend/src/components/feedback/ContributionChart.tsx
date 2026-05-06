'use client'

import type { Contribution } from '@/lib/types'

interface ContributionChartProps {
  contribution: Contribution
}

const bars = [
  { key: 'alpha_F' as const, label: '재무(F)', color: 'bg-blue-500' },
  { key: 'beta_T'  as const, label: '기술(T)', color: 'bg-purple-500' },
  { key: 'gamma_G' as const, label: '정책(G)', color: 'bg-emerald-500' },
]

export default function ContributionChart({ contribution }: ContributionChartProps) {
  const total = contribution.alpha_F + contribution.beta_T + contribution.gamma_G || 1

  return (
    <div className="space-y-2">
      {bars.map(({ key, label, color }) => {
        const pct = ((contribution[key] / total) * 100).toFixed(0)
        return (
          <div key={key} className="flex items-center gap-2">
            <span className="text-xs text-gray-500 w-14 shrink-0">{label}</span>
            <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
              <div
                className={`h-full ${color} rounded transition-all`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-xs font-medium text-gray-700 w-8 text-right">{pct}%</span>
          </div>
        )
      })}
    </div>
  )
}
