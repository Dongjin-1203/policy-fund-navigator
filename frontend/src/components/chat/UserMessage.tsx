'use client'

import MessageBubble from './MessageBubble'

interface UserMessageProps {
  content: string
}

export default function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <MessageBubble role="user">{content}</MessageBubble>
    </div>
  )
}
