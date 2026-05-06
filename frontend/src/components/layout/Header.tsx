'use client'

import { Bot } from 'lucide-react'

export default function Header() {
  return (
    <header className="h-14 bg-[#1B4FD8] flex items-center px-4 gap-3 shrink-0">
      <Bot className="w-6 h-6 text-white" />
      <div>
        <p className="text-white font-semibold text-sm leading-tight">중진공 AI 자금 네비게이터</p>
        <p className="text-blue-200 text-xs">중소기업진흥공단 정책자금 매칭 서비스</p>
      </div>
    </header>
  )
}
