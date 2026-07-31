import { http } from './http'
import type { OnlineTermResult } from '../types/api'

export type OnlineTermSource = 'wikipedia' | 'iate' | 'linguee'

export interface OnlineTermQueryResponse {
  query: string
  items: OnlineTermResult[]
  source_language: string
  target_language: string
  read_only: boolean
}

export async function queryOnlineTerms(
  termBaseId: string,
  sourceText: string,
  sources: OnlineTermSource[],
) {
  const { data } = await http.post<OnlineTermQueryResponse>(
    `/term-bases/${termBaseId}/online-query`,
    { source_text: sourceText, sources },
  )
  return data
}
