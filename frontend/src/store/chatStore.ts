import { create } from 'zustand'
import type { Message } from '@/lib/types'

interface ChatStore {
  messages: Message[]
  company_id: string | null
  isLoading: boolean
  addMessage: (msg: Omit<Message, 'id' | 'timestamp'>) => void
  setLoading: (loading: boolean) => void
  setCompanyId: (id: string) => void
  clearChat: () => void
}

export const useChatStore = create<ChatStore>((set) => ({
  messages: [],
  company_id: null,
  isLoading: false,

  addMessage: (msg) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { ...msg, id: crypto.randomUUID(), timestamp: new Date() },
      ],
    })),

  setLoading: (loading) => set({ isLoading: loading }),

  setCompanyId: (id) => set({ company_id: id }),

  clearChat: () => set({ messages: [], company_id: null, isLoading: false }),
}))
