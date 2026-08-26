import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发服务器把 API 转发到本机 FastAPI，避免浏览器跨域配置干扰学习流程。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
