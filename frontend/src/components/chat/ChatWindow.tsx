'use client'

import { useEffect, useRef } from 'react'
import { ScrollArea } from '@radix-ui/react-scroll-area'
import { useChatStore } from '@/store/chatStore'
import type { Message, MatchResponse, FeedbackResponse } from '@/lib/types'
import BotMessage from './BotMessage'
import UserMessage from './UserMessage'
import ProgramList from '@/components/results/ProgramList'
import FeedbackPanel from '@/components/feedback/FeedbackPanel'
import FinancialForm from '@/components/forms/FinancialForm'

function renderBotContent(msg: Message) {
  switch (msg.type) {
    case 'programs':
      return <ProgramList data={msg.data as MatchResponse} />
    case 'feedback':
      return <FeedbackPanel data={msg.data as FeedbackResponse} />
    case 'financial_form':
      return <FinancialForm />
    default:
      return <span className="whitespace-pre-wrap">{msg.content}</span>
  }
}

export default function ChatWindow() {
  const { messages, isLoading } = useChatStore()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  return (
    <ScrollArea className="flex-1 overflow-y-auto">
      <div className="px-4 py-6 space-y-4 max-w-3xl mx-auto">
        {messages.map((msg) =>
          msg.role === 'user' ? (
            <UserMessage key={msg.id} content={msg.content} />
          ) : (
            <BotMessage key={msg.id}>
              {renderBotContent(msg)}
            </BotMessage>
          )
        )}
        {isLoading && <BotMessage loading />}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
