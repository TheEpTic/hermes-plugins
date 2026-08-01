import React, { useEffect, useState } from 'react'
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
let rest = null
const el = React.createElement

function api(path, options) {
  if (!rest) return Promise.reject(new Error('hermes-sfw api not ready'))
  return rest(path, options)
}

function useSFWStatus() {
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
      if (!cancelled) setError(value instanceof Error ? value.message : 'dependency guard unavailable')
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

function SFWStatusChip() {
  const { status, error } = useSFWStatus()
  const ready = Boolean(status?.installed) && !error
  const label = error ? 'sfw: error' : status ? (ready ? `sfw: ${status.version || 'ready'}` : 'sfw: missing') : 'sfw: checking'
  return el(Tip, { label: error || 'open dependency guard' }, el('button', {
    className: `inline-flex h-full items-center gap-1 rounded-none px-1.5 text-[0.6875rem] transition-colors hover:bg-(--chrome-action-hover) ${ready ? 'text-(--ui-text-tertiary)' : 'text-red-400'}`,
    onClick: () => host.navigate('/sfw-guard'),
    type: 'button'
  }, el(Codicon, { name: ready ? 'shield' : 'error', size: '0.7rem' }), label))
}

function SFWPage() {
  const { status, error, refresh } = useSFWStatus()
  const [command, setCommand] = useState('')
  const [workdir, setWorkdir] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const run = async event => {
    event.preventDefault()
    if (!command.trim() || !status?.installed) return
    if (!window.confirm('run this dependency operation through the SFW dependency guard?')) return
    setBusy(true)
    setActionError(null)
    try {
      const value = await api('/run', {
        method: 'POST',
        body: { command, workdir: workdir || null, confirm: true }
      })
      setResult(value)
    } catch (value) {
      setActionError(value instanceof Error ? value.message : 'dependency operation failed')
    } finally {
      setBusy(false)
    }
  }

  if (error && !status) return el(ErrorState, { title: 'SFW dependency guard unavailable', description: error })
  if (!status) return el(EmptyState, { title: 'Loading dependency guard', description: 'Checking the local SFW binary and policy state.' })

  return el('div', { className: 'flex h-full min-h-0 flex-col gap-4 overflow-auto p-5' },
    el('div', { className: 'flex flex-wrap items-center justify-between gap-3' },
      el('div', null,
        el('h1', { className: 'text-lg font-semibold' }, 'SFW dependency guard'),
        el('p', { className: 'max-w-2xl text-sm text-muted-foreground' }, 'This is a dependency guard, not a sandbox. Package lifecycle scripts and build backends may still execute.'),
        el(Badge, { className: 'mt-2', variant: status.installed ? 'outline' : 'destructive' }, status.installed ? `available · ${status.version || 'version unknown'}` : 'binary unavailable')
      ),
      el(Button, { onClick: refresh, size: 'sm', variant: 'outline' }, 'refresh')
    ),
    actionError && el('div', { className: 'rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive' }, actionError),
    el('section', { className: 'rounded-lg border border-border/60 p-4' },
      el('div', { className: 'grid gap-3 text-sm md:grid-cols-3' },
        el('div', null, el('div', { className: 'text-xs uppercase tracking-[0.12em] text-muted-foreground' }, 'binary'), el('div', { className: 'mt-1 font-medium' }, status.binary || 'not found')),
        el('div', null, el('div', { className: 'text-xs uppercase tracking-[0.12em] text-muted-foreground' }, 'direct terminal policy'), el('div', { className: 'mt-1 font-medium' }, status.direct_terminal_enforced ? 'enforced' : 'not enforced')),
        el('div', null, el('div', { className: 'text-xs uppercase tracking-[0.12em] text-muted-foreground' }, 'execution'), el('div', { className: 'mt-1 font-medium' }, 'explicit confirmation required'))
      )
    ),
    el('section', { className: 'rounded-lg border border-border/60 p-4' },
      el('h2', { className: 'mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground' }, 'confirmed dependency operation'),
      el('form', { className: 'grid gap-2', onSubmit: run },
        el(Input, { disabled: !status.installed, onChange: event => setCommand(event.target.value), placeholder: 'e.g. npm install', value: command }),
        el(Input, { disabled: !status.installed, onChange: event => setWorkdir(event.target.value), placeholder: 'workdir (optional, selected explicitly)', value: workdir }),
        el(Button, { disabled: busy || !status.installed || !command.trim(), type: 'submit' }, busy ? 'running…' : 'run through guard')
      ),
      result && el(LogView, { className: 'mt-3 max-h-72 border border-border/50' }, [result.stdout, result.stderr].filter(Boolean).join('\n') || `exit ${result.exit_code ?? 'unknown'}`)
    )
  )
}

const plugin = {
  id: 'hermes-sfw',
  name: 'SFW Dependency Guard',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    ctx.onDispose(() => { rest = null })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/sfw-guard' }, render: () => el(SFWPage) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, order: 53, data: { codicon: 'shield', label: 'SFW dependency guard', path: '/sfw-guard' } },
      { id: 'status', area: STATUSBAR_AREAS.right, order: 83, render: () => el(SFWStatusChip) },
      { id: 'open', area: PALETTE_AREA, data: { id: 'hermes-sfw.open', label: 'SFW: Open dependency guard', keywords: ['sfw', 'dependencies', 'security', 'guard'], run: () => host.navigate('/sfw-guard') } }
    ])
  }
}

export default plugin
