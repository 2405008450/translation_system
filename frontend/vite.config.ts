import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath, URL } from 'node:url'

const projectRoot = fileURLToPath(new URL('.', import.meta.url))

function createBuildVersion() {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return [
    now.getUTCFullYear(),
    pad(now.getUTCMonth() + 1),
    pad(now.getUTCDate()),
  ].join('.') + `-${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`
}

function resolveAppVersion(env: Record<string, string>, isBuild: boolean) {
  const configuredVersion = env.VITE_APP_VERSION
    || env.APP_VERSION
    || process.env.VITE_APP_VERSION
    || process.env.APP_VERSION
  if (configuredVersion?.trim()) {
    return configuredVersion.trim()
  }
  return isBuild ? createBuildVersion() : 'dev'
}

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, projectRoot, '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://0.0.0.0:19013'
  const appVersion = resolveAppVersion(env, command === 'build')

  return {
    root: projectRoot,
    plugins: [
      vue(),
      {
        name: 'write-app-version',
        apply: 'build',
        closeBundle() {
          writeFileSync(resolve(projectRoot, 'dist', 'app-version.txt'), `${appVersion}\n`, 'utf8')
        },
      },
    ],
    define: {
      __APP_VERSION__: JSON.stringify(appVersion),
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
