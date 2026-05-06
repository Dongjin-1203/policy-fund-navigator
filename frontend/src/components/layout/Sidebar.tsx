'use client'

import { MessageSquare, Trash2 } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'

export default function Sidebar() {
  const { messages, company_id, clearChat } = useChatStore()

  const sessions = company_id
    ? [{ id: company_id, label: `사업자번호: ${company_id}`, count: messages.length }]
    : []

  return (
    <aside className="w-64 bg-white border-r border-gray-100 flex flex-col shrink-0">
      <div className="px-4 py-4 border-b border-gray-100">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">상담 이력</p>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {sessions.length === 0 ? (
          <p className="text-xs text-gray-300 text-center mt-8">상담 이력이 없습니다.</p>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-50 text-blue-700"
            >
              <MessageSquare className="w-3.5 h-3.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium truncate">{s.label}</p>
                <p className="text-xs text-blue-400">{s.count}개 메시지</p>
              </div>
            </div>
          ))
        )}
      </div>

      {sessions.length > 0 && (
        <div className="p-3 border-t border-gray-100">
          <button
            onClick={clearChat}
            className="w-full flex items-center justify-center gap-1.5 py-2 text-xs text-gray-400 hover:text-red-500 transition-colors rounded-lg hover:bg-red-50"
          >
            <Trash2 className="w-3 h-3" />
            대화 초기화
          </button>
        </div>
      )}
    </aside>
  )
}
