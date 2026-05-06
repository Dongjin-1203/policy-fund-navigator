import type { Metadata } from 'next'
import './globals.css'
import Header from '@/components/layout/Header'
import Sidebar from '@/components/layout/Sidebar'

export const metadata: Metadata = {
  title: '중진공 AI 자금 네비게이터',
  description: '중소기업 정책자금 AI 매칭 서비스',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className="h-full">
      <body className="h-full flex flex-col bg-[#F8F9FA] antialiased">
        <Header />
        <div className="flex flex-1 min-h-0">
          <Sidebar />
          <main className="flex flex-1 flex-col min-h-0 min-w-0">
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
