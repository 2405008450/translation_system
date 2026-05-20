import { existsSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')
const resetScript = path.join(rootDir, 'scripts', 'e2e_reset_db.py')

const candidates = [
  process.env.PYTHON,
  path.join(rootDir, '.venv', 'Scripts', 'python.exe'),
  path.join(rootDir, '.venv', 'bin', 'python'),
  'python',
].filter(Boolean)

let lastResult = null

for (const pythonPath of candidates) {
  if (pythonPath.includes(path.sep) && !existsSync(pythonPath)) {
    continue
  }

  const result = spawnSync(pythonPath, [resetScript], {
    cwd: rootDir,
    env: process.env,
    stdio: 'inherit',
    shell: false,
  })
  lastResult = result

  if (result.error?.code === 'ENOENT') {
    continue
  }

  process.exit(result.status ?? 1)
}

if (lastResult?.error) {
  console.error(lastResult.error.message)
}
console.error('未找到可用的 Python。请安装依赖或设置 PYTHON 指向项目虚拟环境。')
process.exit(1)
