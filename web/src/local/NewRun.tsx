// The local run form (spec "运行页", 本地版): task, type, data source and config. Data sources are
// offered by name only; a refused submit shows the backend's reason.

import { useEffect, useRef, useState, type FormEvent } from 'react'

import type { LiveSource, RunOptions } from '../data/source'
import { errorText } from '../format'
import type { TaskType } from '../run-view/model'

const DEFAULT_CONFIG = 'full'

export function NewRun({ live, onStarted, onClose }: { live: LiveSource; onStarted: (id: string) => void; onClose: () => void }) {
  const [options, setOptions] = useState<RunOptions | null>(null)
  const [task, setTask] = useState('')
  const [taskType, setTaskType] = useState<TaskType>('query')
  const [dataSource, setDataSource] = useState('')
  const [config, setConfig] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const taskBox = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    let gone = false
    live
      .options()
      .then((o) => {
        if (gone) return
        setOptions(o)
        setDataSource((d) => d || (o.data_sources[0] ?? ''))
        setConfig((c) => c || (o.configs.includes(DEFAULT_CONFIG) ? DEFAULT_CONFIG : (o.configs[0] ?? '')))
      })
      .catch((e: unknown) => !gone && setError(`Could not load the form's options: ${errorText(e)}`))
    taskBox.current?.focus()
    return () => {
      gone = true
    }
  }, [live])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onStarted(await live.submit({ task, task_type: taskType, data_source: dataSource, config }))
    } catch (err: unknown) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const noSources = options !== null && options.data_sources.length === 0
  return (
    <form className="pane p-3 flex flex-col gap-2.5 w-[560px] max-w-[calc(100vw-24px)] shadow-lg" onSubmit={submit} aria-label="New run">
      <h2 className="text-[13px] font-semibold">New run</h2>
      <label className="field">
        Task
        <textarea ref={taskBox} value={task} onChange={(e) => setTask(e.target.value)} placeholder="A business question, or a dbt task" required />
      </label>
      <div className="grid grid-cols-3 gap-2">
        <label className="field">
          Type
          <select value={taskType} onChange={(e) => setTaskType(e.target.value as TaskType)}>
            {(options?.task_types ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Data source
          <select value={dataSource} onChange={(e) => setDataSource(e.target.value)} disabled={noSources}>
            {noSources && <option value="">none configured</option>}
            {options?.data_sources.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Config
          <select value={config} onChange={(e) => setConfig(e.target.value)}>
            {options?.configs.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {noSources && (
        <div className="text-[11.5px] text-ink-3">
          The server was started without data sources: start it with <code className="mono">data-agent web --data-sources FILE</code>.
        </div>
      )}
      {error && (
        <div className="banner banner-fail" role="alert">
          {error}
        </div>
      )}
      <div className="flex items-center gap-2">
        <div className="flex-1" />
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy || options === null}>
          {busy ? 'Starting…' : 'Start run'}
        </button>
      </div>
    </form>
  )
}
