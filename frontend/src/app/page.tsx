'use client'

import { useEffect } from 'react'
import { useChatStore } from '@/store/chatStore'
import { matchCompany } from '@/lib/api'
import { normalizeMatchResponse } from '@/lib/normalize'
import { isCompanyId } from '@/components/chat/ChatInput'
import ChatWindow from '@/components/chat/ChatWindow'
import ChatInput from '@/components/chat/ChatInput'
import type { MatchResponse } from '@/lib/types'

const GREETING =
  '안녕하세요! 중진공 AI 자금 네비게이터입니다.\n사업자번호를 입력해주세요. (예: 123-45-67890)'

export default function HomePage() {
  const { messages, isLoading, addMessage, setLoading, setCompanyId } = useChatStore()

  useEffect(() => {
    if (messages.length === 0) {
      addMessage({ role: 'bot', type: 'text', content: GREETING })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSend = async (text: string) => {
    addMessage({ role: 'user', type: 'text', content: text })

    if (!isCompanyId(text)) {
      addMessage({
        role: 'bot',
        type: 'text',
        content: '사업자번호 형식(예: 123-45-67890 또는 숫자 10자리)으로 입력해 주세요.',
      })
      return
    }

    const companyId = text.replace(/-/g, '')
    setCompanyId(companyId)
    setLoading(true)

    try {
      const raw: MatchResponse = await matchCompany(companyId)
      const result = normalizeMatchResponse(raw)

      if (result.user_input_required) {
        addMessage({
          role: 'bot',
          type: 'financial_form',
          content: '재무 정보를 입력해 주세요.',
        })
        return
      }

      if ((result.programs?.length ?? 0) > 0) {
        addMessage({ role: 'bot', type: 'programs', content: '', data: result })
      } else {
        addMessage({
          role: 'bot',
          type: 'text',
          content: result.feedback ?? '현재 조건에 맞는 사업을 찾지 못했습니다.',
        })
      }
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : '알 수 없는 오류가 발생했습니다.'
      addMessage({
        role: 'bot',
        type: 'text',
        content: `매칭 처리 중 오류가 발생했습니다: ${msg}`,
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <ChatWindow />
      <ChatInput onSend={handleSend} disabled={isLoading} />
    </div>
  )
}
