import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 로컬 개발: SSH 터널(ssh -N -L 8000:127.0.0.1:8000 -p 2223 cerins@115.178.66.69)로 서버 API에 연결
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
