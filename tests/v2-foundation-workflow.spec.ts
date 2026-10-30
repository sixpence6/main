import { describe, expect, it, vi } from 'vitest'
import { getWorkflow, runWorkflowAction } from '@/api/workflow'

const view = {
  project_id: 'p1',
  run_id: 'r1',
  stage: 'S01',
  status: 'active',
  project_revision: 3,
  requirement_revision: 3,
  intent_route: null,
  intent_action: null,
  issue: null,
  artifacts: [],
  approvals: [],
  available_actions: [],
  legacy: [],
  generation_job: null,
  delivery: null,
  updated_at: '2026-01-01T00:00:00.000Z',
}

describe('v2 workflow API', () => {
  it('reads the workflow view for a project', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(view), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getWorkflow('p1')).resolves.toMatchObject({ run_id: 'r1', stage: 'S01' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/p1/workflow',
      expect.objectContaining({ headers: expect.any(Headers) }),
    )
  })

  it('posts an action with the revision and requested text', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(view), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await runWorkflowAction('p1', 'submit_request', {
      expected_revision: 3,
      text: '做一张白底商品图',
    })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/projects/p1/workflow/actions/submit_request',
    )
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      expected_revision: 3,
      text: '做一张白底商品图',
    })
  })
})
