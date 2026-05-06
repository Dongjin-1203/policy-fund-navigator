'use client'

import { Bot } from 'lucide-react'
import MessageBubble from './MessageBubble'

interface BotMessageProps {
  children?: React.ReactNode
  loading?: boolean
}

export default function BotMessage({ children, loading }: BotMessageProps) {
  return (
    <div className="flex items-end gap-2">
      <div className="w-8 h-8 rounded-full bg-[#1B4FD8] flex items-center justify-center shrink-0">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <MessageBubble role="bot">
        {loading ? (
          <span className="flex gap-1 items-center h-4">
            <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
            <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
            <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" />
          </span>
        ) : children}
      </MessageBubble>
    </div>
  )
}
