import React, { useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  Input,
  LogView,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  host
} from '@hermes/plugin-sdk'

const POLL_MS = 30_000
const OUTPUT_LIMIT = 32_768
let rest = null

const el = React.createElement

function api(path, options) {
  if (!rest) return Promise.reject(new Error('hermes-ssh api not ready'))
  return rest(path, options)
}

function useSSHStatus() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [generation, setGeneration] = useState(0)
  useEffect(() => {
    let cancelled = false
    const refresh = () => api('/status').then(value => {
      if (!cancelled) {
        setStatus(value)
        setError(null)
      }
    }).catch(value => {
      if (!cancelled) setError(value instanceof Error ? value.message : 'SSH status unavailable')
    })
    void refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [generation])
  return { status, error, refresh: () => setGeneration(value => value + 1) }
}

function statusLabel(status) {
  if (!status) return 'ssh: checking'
  const counts = status.session_counts || {}
  const active = Number(counts.active || 0)
  const idle = Number(status.idle_session_count || 0)
  return `ssh: ${active} active / ${idle} idle`
}

function SSHStatusChip() {
  const { status, error } = useSSHStatus()
  return el(Tip, { label: error || 'open SSH operations' }, el('button', {
    className: 'inline-flex h-full items-center gap-1 rounded-none px-1.5 text-[0.6875rem] tabular-nums text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
    onClick: () => host.navigate('/ssh-operations'),
    type: 'button'
  }, el(Codicon, { name: error ? 'error' : 'remote', size: '0.7rem' }), statusLabel(status)))
}

function SectionTitle({ children }) {
  return el('h2', { className: 'mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground' }, children)
}

function SSHPage() {
  const { status, error, refresh } = useSSHStatus()
  const [selectedSession, setSelectedSession] = useState(null)
  const [machine, setMachine] = useState('')
  const [command, setCommand] = useState('')
  const [terminal, setTerminal] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const machines = status?.machines || []
  const sessions = status?.sessions || []
  const audit = status?.audit || []
  const selected = useMemo(() => sessions.find(item => item.id === selectedSession) || null, [sessions, selectedSession])

  useEffect(() => {
    if (!machine && machines[0]) setMachine(machines[0].name)
    if (selectedSession && !selected) setSelectedSession(null)
  }, [machine, machines, selected, selectedSession])

  const refreshOutput = async sessionId => {
    setActionError(null)
    try {
      const result = await api(`/sessions/${encodeURIComponent(sessionId)}/poll`, { method: 'POST', body: {} })
      setTerminal(result)
    } catch (value) {
      setActionError(value instanceof Error ? value.message : 'session poll failed')
    }
  }

  const kill = async sessionId => {
    if (!window.confirm(`kill SSH session ${sessionId}?`)) return
    setBusy(true)
    setActionError(null)
    try {
      await api(`/sessions/${encodeURIComponent(sessionId)}/kill`, { method: 'POST', body: { confirm: true } })
      setSelectedSession(null)
      host.notify({ kind: 'success', message: 'SSH session killed' })
    } catch (value) {
      setActionError(value instanceof Error ? value.message : 'session kill failed')
    } finally {
      setBusy(false)
    }
  }

  const run = async event => {
    event.preventDefault()
    if (!machine || !command.trim()) return
    if (!window.confirm(`run this command on ${machine}?`)) return
    setBusy(true)
    setActionError(null)
    try {
      const result = await api('/terminal', {
        method: 'POST',
        body: { machine, command, confirm: true, max_output_chars: OUTPUT_LIMIT }
      })
      setTerminal(result)
      setCommand('')
    } catch (value) {
      setActionError(value instanceof Error ? value.message : 'SSH command failed')
    } finally {
      setBusy(false)
    }
  }

  if (error && !status) return el(ErrorState, { title: 'SSH operations unavailable', description: error })
  if (!status) return el(EmptyState, { title: 'Loading SSH operations', description: 'Reading shared machine and session state.' })

  return el('div', { className: 'flex h-full min-h-0 flex-col gap-4 overflow-auto p-5' },
    el('div', { className: 'flex flex-wrap items-center justify-between gap-3' },
      el('div', null,
        el('h1', { className: 'text-lg font-semibold' }, 'SSH operations'),
        el('p', { className: 'text-sm text-muted-foreground' }, 'Shared inventory. Commands and destructive actions require a second confirmation.'),
        el(Badge, { className: 'mt-2', variant: 'outline' }, `${status.machine_count} machine${status.machine_count === 1 ? '' : 's'} · ${status.active_session_count} active session${status.active_session_count === 1 ? '' : 's'}`)
      ),
      el(Button, { onClick: refresh, size: 'sm', variant: 'outline' }, 'refresh')
    ),
    actionError && el('div', { className: 'rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive' }, actionError),
    el('div', { className: 'grid gap-4 xl:grid-cols-2' },
      el('section', { className: 'rounded-lg border border-border/60 p-4' },
        el(SectionTitle, null, 'machines'),
        machines.length === 0
          ? el('p', { className: 'text-sm text-muted-foreground' }, 'No registered machines.')
          : el('div', { className: 'space-y-2' }, machines.map(item => el('div', { className: 'flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2', key: item.name },
              el('div', { className: 'min-w-0' },
                el('div', { className: 'truncate text-sm font-medium' }, item.name),
                el('div', { className: 'truncate text-xs text-muted-foreground' }, `${item.user}@${item.host}:${item.port}`)
              ),
              el(Button, { disabled: busy, onClick: async () => {
                setBusy(true)
                try {
                  const result = await api(`/machines/${encodeURIComponent(item.name)}/test`, { method: 'POST', body: {} })
                  host.notify({ kind: result.success ? 'success' : 'error', message: `SSH ${item.name}: ${result.status}` })
                } catch (value) {
                  setActionError(value instanceof Error ? value.message : 'machine test failed')
                } finally {
                  setBusy(false)
                }
              }, size: 'xs', variant: 'ghost' }, 'test')
            )))
      ),
      el('section', { className: 'rounded-lg border border-border/60 p-4' },
        el(SectionTitle, null, 'sessions'),
        sessions.length === 0
          ? el('p', { className: 'text-sm text-muted-foreground' }, 'No active sessions.')
          : el('div', { className: 'space-y-2' }, sessions.map(item => el('div', { className: 'flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2', key: item.id },
              el('button', { className: 'min-w-0 text-left', onClick: () => { setSelectedSession(item.id); void refreshOutput(item.id) }, type: 'button' },
                el('div', { className: 'truncate text-sm font-medium' }, `${item.machine} · ${item.id}`),
                el('div', { className: 'text-xs text-muted-foreground' }, `${item.status || 'unknown'} · ${item.command_count} commands`)
              ),
              el(Button, { disabled: busy, onClick: () => void kill(item.id), size: 'xs', variant: 'destructive' }, 'kill')
            )))
      )
    ),
    el('section', { className: 'rounded-lg border border-border/60 p-4' },
      el(SectionTitle, null, 'confirmed terminal'),
      el('form', { className: 'grid gap-2 md:grid-cols-[12rem_1fr_auto]', onSubmit: run },
        el('select', { className: 'h-9 rounded-md border border-input bg-background px-2 text-sm', onChange: event => setMachine(event.target.value), value: machine }, machines.map(item => el('option', { key: item.name, value: item.name }, item.name))),
        el(Input, { onChange: event => setCommand(event.target.value), placeholder: 'command to run on the selected machine', value: command }),
        el(Button, { disabled: busy || !machine || !command.trim(), type: 'submit' }, busy ? 'running…' : 'run')
      ),
      terminal && el(LogView, { className: 'mt-3 max-h-72 border border-border/50' }, [terminal.stdout, terminal.stderr].filter(Boolean).join('\n'))
    ),
    selected && el('section', { className: 'rounded-lg border border-border/60 p-4' },
      el('div', { className: 'flex items-center justify-between gap-3' }, el(SectionTitle, null, `session ${selected.id}`), el(Button, { onClick: () => void refreshOutput(selected.id), size: 'xs', variant: 'outline' }, 'poll output')),
      terminal && el(LogView, { className: 'max-h-72 border border-border/50' }, [terminal.stdout, terminal.stderr].filter(Boolean).join('\n'))
    ),
    el('section', { className: 'rounded-lg border border-border/60 p-4' },
      el(SectionTitle, null, 'audit metadata'),
      audit.length === 0
        ? el('p', { className: 'text-sm text-muted-foreground' }, 'No command audit entries.')
        : el('div', { className: 'space-y-1 text-xs text-muted-foreground' }, audit.slice(-20).reverse().map(item => el('div', { className: 'flex flex-wrap gap-x-3 gap-y-1 rounded bg-muted/20 px-2 py-1', key: `${item.timestamp}-${item.command_sha256}` }, `${item.timestamp} · ${item.machine} · sha256:${item.command_sha256 || 'n/a'} · exit ${item.exit_code ?? 'n/a'}`)))
    )
  )
}

const plugin = {
  id: 'hermes-ssh',
  name: 'SSH Operations',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    ctx.onDispose(() => { rest = null })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/ssh-operations' }, render: () => el(SSHPage) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, order: 52, data: { codicon: 'remote', label: 'SSH operations', path: '/ssh-operations' } },
      { id: 'status', area: STATUSBAR_AREAS.right, order: 82, render: () => el(SSHStatusChip) },
      { id: 'open', area: PALETTE_AREA, data: { id: 'hermes-ssh.open', label: 'SSH: Open operations', keywords: ['ssh', 'remote', 'machines', 'sessions'], run: () => host.navigate('/ssh-operations') } }
    ])
  }
}

export default plugin
