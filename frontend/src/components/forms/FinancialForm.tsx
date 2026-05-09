'use client'

import { useState } from 'react'
import type { FinancialData, MatchResponse } from '@/lib/types'
import { matchCompany } from '@/lib/api'
import { useChatStore } from '@/store/chatStore'
import { normalizeMatchResponse } from '@/lib/normalize'

export default function FinancialForm() {
  const { company_id, addMessage, setLoading } = useChatStore()
  const [form, setForm] = useState<FinancialData>({
    is_venture: false,
    is_innobiz: false,
  })
  const [submitted, setSubmitted] = useState(false)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target
    setForm((prev) => ({
      ...prev,
      [name]:
        type === 'checkbox' ? checked
        : type === 'text'   ? (value === '' ? undefined : value)
        :                     (value === '' ? undefined : Number(value)),
    }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!company_id) return
    setSubmitted(true)
    setLoading(true)

    addMessage({ role: 'user', type: 'text', content: '재무 정보를 입력했습니다.' })

    try {
      const raw: MatchResponse = await matchCompany(company_id, form)
      const result = normalizeMatchResponse(raw)
      if ((result.programs?.length ?? 0) > 0) {
        addMessage({
          role: 'bot',
          type: 'programs',
          content: '',
          data: result,
        })
      } else {
        addMessage({
          role: 'bot',
          type: 'text',
          content: result.feedback ?? '현재 조건에 맞는 사업을 찾지 못했습니다.',
        })
      }
    } catch {
      addMessage({
        role: 'bot',
        type: 'text',
        content: '매칭 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
      })
    } finally {
      setLoading(false)
    }
  }

  if (submitted) {
    return <p className="text-xs text-gray-400 italic">재무 정보가 제출되었습니다.</p>
  }

  const field = (label: string, name: keyof FinancialData, unit?: string) => (
    <div className="flex items-center gap-2">
      <label className="text-xs text-gray-600 w-28 shrink-0">{label}</label>
      <div className="flex-1 flex items-center gap-1">
        <input
          type="number"
          name={name as string}
          onChange={handleChange}
          placeholder="0"
          className="w-full rounded border border-gray-200 px-2 py-1 text-xs focus:outline-none focus:border-blue-400"
        />
        {unit && <span className="text-xs text-gray-400">{unit}</span>}
      </div>
    </div>
  )

  const textField = (label: string, name: keyof FinancialData, placeholder?: string) => (
    <div className="flex items-center gap-2">
      <label className="text-xs text-gray-600 w-28 shrink-0">{label}</label>
      <input
        type="text"
        name={name as string}
        onChange={handleChange}
        placeholder={placeholder ?? ''}
        className="flex-1 rounded border border-gray-200 px-2 py-1 text-xs focus:outline-none focus:border-blue-400"
      />
    </div>
  )

  return (
    <form onSubmit={handleSubmit} className="space-y-3 text-sm">
      <p className="text-xs text-gray-500 mb-2">
        공개된 재무 데이터를 찾지 못했습니다. 아래 항목을 직접 입력해 주세요.
      </p>
      {field('매출액',         'revenue',          '원')}
      {field('영업이익',       'operating_profit',  '원')}
      {field('부채비율',       'debt_ratio',        '%')}
      {field('현금흐름',       'cash_flow',         '원')}
      {field('종업원수',       'employee_count',    '명')}
      {field('업력',           'business_age',      '년')}

      <div className="flex gap-4">
        <label className="flex items-center gap-1 text-xs text-gray-600 cursor-pointer">
          <input type="checkbox" name="is_venture" onChange={handleChange} className="rounded" />
          벤처기업
        </label>
        <label className="flex items-center gap-1 text-xs text-gray-600 cursor-pointer">
          <input type="checkbox" name="is_innobiz" onChange={handleChange} className="rounded" />
          이노비즈
        </label>
      </div>

      {textField('회사명', 'corp_name', '예) (주)테크스타트')}
      {field('특허 보유수', 'patent_count', '건')}

      <button
        type="submit"
        className="w-full py-2 bg-[#1B4FD8] text-white text-xs font-medium rounded-lg hover:bg-blue-700 transition-colors"
      >
        매칭 시작
      </button>
    </form>
  )
}
