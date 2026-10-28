import { expect, test } from '@playwright/test'
import { computeDiff } from '../src/utils/textDiff'

const samples = [
  ['⟦MATH_1⟧ Cylindrical Pin', '⟦MATH_1⟧ Dowel pin'],
  ['Compression Spring Φ1⟦MATH_1⟧', 'Compression spring Φ1⟦MATH_1⟧'],
  ['⟦MATH1⟧ Dowel pin', '⟦MATH_1⟧ Dowel pin'],
  ['RV_NP01_01_HOST01', 'RV_NP01_01_HOST02'],
  ['__name__', '_name_'],
  ['___', '___'],
  ['', '_'],
  ['_', ''],
  ['旧文㐀𠀀かな한글 αβγ résumé🙂\n⟦MATH_1⟧', '新文㐀𠀁カナ한글 αβδ résumé🙂\n⟦MATH_1⟧'],
]

for (const [before, after] of samples) {
  test(`差异文本保真：${JSON.stringify(before)} → ${JSON.stringify(after)}`, () => {
    const diff = computeDiff(before!, after!)
    expect(diff.filter(part => part.type !== 'insert').map(part => part.text).join('')).toBe(before)
    expect(diff.filter(part => part.type !== 'delete').map(part => part.text).join('')).toBe(after)
    // 编辑器从差异 DOM 序列化时会跳过删除节点；反复渲染、保存也不能丢字符。
    let saved = after!
    for (let index = 0; index < 3; index++) {
      saved = computeDiff(before!, saved).filter(part => part.type !== 'delete').map(part => part.text).join('')
      expect(saved).toBe(after)
    }
  })
}
