import { invoke } from '@tauri-apps/api/core'

export type SkillSummary = {
  name: string
  agent: string
  path: string
  resolvedPath: string
  description: string
  linked: boolean
  shared: boolean
  bundled: boolean
  fromAlethe: boolean
  entryCount: number
}

export type SkillAgentSnapshot = {
  agent: string
  root: string | null
  exists: boolean
  skills: SkillSummary[]
}

export type SkillNode = {
  name: string
  path: string
  isDir: boolean
  size: number
  children: SkillNode[]
  truncated: boolean
}

export type SkillLockInfo = {
  source: string | null
  sourceUrl: string | null
  installedAt: string | null
  updatedAt: string | null
}

export type SkillDetail = {
  summary: SkillSummary
  frontmatter: Record<string, string>
  frontmatterRaw: string
  body: string
  tree: SkillNode[]
  lock: SkillLockInfo | null
}

export type SkillRemoveReport = {
  path: string
  removedLinkOnly: boolean
  sharedCopyPath: string | null
}

export type BundledSkillState = 'available' | 'installed' | 'update' | 'conflict'

export type BundledSkillAgent = {
  agent: string
  state: BundledSkillState
  path: string | null
}

export type BundledSkill = {
  name: string
  description: string
  version: number
  body: string
  agents: BundledSkillAgent[]
}

export async function skillsScan(): Promise<SkillAgentSnapshot[]> {
  return invoke<SkillAgentSnapshot[]>('skills_scan')
}

export async function skillsDetail(agent: string, name: string): Promise<SkillDetail> {
  return invoke<SkillDetail>('skills_detail', { agent, name })
}

export async function skillsUninstall(agent: string, name: string): Promise<SkillRemoveReport> {
  return invoke<SkillRemoveReport>('skills_uninstall', { agent, name })
}

export async function skillsCatalog(): Promise<BundledSkill[]> {
  return invoke<BundledSkill[]>('skills_catalog')
}

export async function skillsInstallBundled(
  name: string,
  agents: string[],
): Promise<BundledSkillAgent[]> {
  return invoke<BundledSkillAgent[]>('skills_install_bundled', { name, agents })
}
