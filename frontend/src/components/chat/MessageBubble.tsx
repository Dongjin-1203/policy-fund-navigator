'use client'

interface MessageBubbleProps {
  role: 'bot' | 'user'
  children: React.ReactNode
}

export default function MessageBubble({ role, children }: MessageBubbleProps) {
  const isUser = role === 'user'
  return (
    <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
      isUser
        ? 'bg-[#1B4FD8] text-white rounded-br-sm'
        : 'bg-white text-gray-800 rounded-bl-sm'
    }`}>
      {children}
    </div>
  )
}
