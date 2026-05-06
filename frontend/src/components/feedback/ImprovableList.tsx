'use client'

import type { ImprovableFeature } from '@/lib/types'
import { TrendingUp } from 'lucide-react'

interface ImprovableListProps {
  items: ImprovableFeature[]
}

export default function ImprovableList({ items }: ImprovableListProps) {
  if (items.length === 0) {
    return <p className="text-xs text-gray-400">보완 가능 항목 없음</p>
  }

  return (
    <ul className="space-y-2">
      {items.map((item) => {
        const label = item.label ?? item.feature
        const delta = item.delta_pct ?? 0
        return (
          <li key={item.feature} className="flex items-start gap-2 text-xs">
            <TrendingUp className="w-3.5 h-3.5 mt-0.5 text-amber-500 shrink-0" />
            <div>
              <span className="font-medium text-gray-700">{label}</span>
              {delta > 0 && (
                <span className="ml-1 text-gray-400">(개선 필요량 {delta.toFixed(1)}%)</span>
              )}
              {item.message && (
                <p className="text-gray-500 mt-0.5">{item.message}</p>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
