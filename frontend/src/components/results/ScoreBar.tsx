'use client'

interface ScoreBarProps {
  score: number
  grade: 'green' | 'yellow' | 'red'
}

const gradeColor: Record<string, string> = {
  green:  'bg-green-500',
  yellow: 'bg-yellow-400',
  red:    'bg-red-500',
}

export default function ScoreBar({ score, grade }: ScoreBarProps) {
  return (
    <div className="w-full">
      <div className="flex justify-between mb-1 text-xs text-gray-500">
        <span>매칭 점수</span>
        <span className="font-semibold text-gray-700">{score.toFixed(1)}점</span>
      </div>
      <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${gradeColor[grade] ?? 'bg-gray-400'}`}
          style={{ width: `${Math.min(score, 100)}%` }}
        />
      </div>
    </div>
  )
}
