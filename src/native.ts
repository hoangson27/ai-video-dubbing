import { invoke } from '@tauri-apps/api/core'
import type { CheckReport, Job, Metadata, Options, ProjectMode, Settings, SetupLog, StageId } from './workflow'

type EngineActions = {
  check: { args: {}; result: CheckReport }
  install_dependencies: { args: {}; result: CheckReport }
  get_setup_log: { args: {}; result: SetupLog }
  probe: { args: { source: string }; result: Metadata }
  prepare_preview: { args: { source: string }; result: { path: string } }
  run: { args: ({ source: string; options: Options } | { job_id: string }) & { verify_packets: boolean; mode?: ProjectMode; stop_after?: StageId }; result: Job }
  create_project: { args: { source: string; options: Options; copy_source?: boolean; new_project?: boolean }; result: Job }
  open_project: { args: { path: string }; result: Job }
  relink_source: { args: { job_id: string; source: string }; result: Job }
  run_stage: { args: { job_id: string; stage: StageId; force?: boolean; verify_packets?: boolean }; result: Job }
  update_project: { args: { job_id: string; options?: Partial<Options>; settings?: Partial<Settings>; mode?: ProjectMode }; result: Job }
  edit_source_segment: { args: { job_id: string; segment_id: string; text: string; start: number; end: number }; result: Job }
  import_artifact: { args: { job_id: string; stage: 'extract' | 'transcribe' | 'translate' | 'synthesize'; path: string; segment_id?: string }; result: Job }
  migrate_project: { args: { job_id: string }; result: Job }
  open_project_folder: { args: { job_id: string }; result: { path?: string } }
  open_project_output: { args: { job_id: string }; result: { path?: string } }
  forget_project: { args: { job_id: string }; result: { path?: string } }
  delete_project: { args: { job_id: string }; result: { path?: string } }
  clean_project: { args: { job_id: string }; result: { path?: string } }
  list_jobs: { args: {}; result: Job[] }
  get_job: { args: { job_id: string }; result: Job }
  preview_voice: { args: { voice: string; speed: number }; result: { path: string } }
  edit_segment: { args: { job_id: string; segment_id: string; text: string }; result: Job }
  retranslate_segment: { args: { job_id: string; segment_id: string }; result: Job }
  regenerate_segment: { args: { job_id: string; segment_id: string }; result: Job }
  export: { args: { job_id: string; verify_packets: boolean }; result: Job }
}

export function engine<A extends keyof EngineActions>(settings: Settings, action: A, args: EngineActions[A]['args']) {
  const { verify_packets: _, ...engineSettings } = settings
  return invoke<EngineActions[A]['result']>('engine_request', {
    request: { action, settings: engineSettings, ...args },
  })
}

export const pickVideo = () => invoke<string | null>('pick_video')
export const pickDirectory = () => invoke<string | null>('pick_directory')
export const cancelEngine = () => invoke<void>('cancel_engine')
export const pauseEngine = () => invoke<void>('pause_engine')
export const pickArtifact = (kind: 'audio' | 'transcript' | 'translation') => invoke<string | null>('pick_artifact', { kind })
export const mediaUrl = (path: string) => invoke<string>('media_url', { path })
