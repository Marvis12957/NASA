import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/react'
import './index.css'
import App from './App.jsx'
import { CLERK_KEY, clerkEnabled } from './lib/auth.jsx'

// ClerkProvider chỉ được mount khi có publishable key — thiếu key mà mount
// sẽ ném lỗi và làm sập app. Chưa có key: app chạy nguyên trạng, auth tắt êm.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    {clerkEnabled ? (
      <ClerkProvider publishableKey={CLERK_KEY}>
        <App />
      </ClerkProvider>
    ) : (
      <App />
    )}
  </StrictMode>,
)
