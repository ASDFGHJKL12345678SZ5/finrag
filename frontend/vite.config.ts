import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// dev 模式经 Vite proxy 转发到 FinRAG API（后端 CORS 已放开，走 proxy 是为与
// datacrew 前端保持一致、也免去端口直连约束）。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5174,
    proxy: {
      '/ask': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8001', changeOrigin: true },
    },
  },
  test: { environment: 'node', include: ['tests/**/*.test.ts'] },
})
