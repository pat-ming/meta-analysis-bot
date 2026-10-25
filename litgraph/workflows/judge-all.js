export const meta = {
  name: 'judge-all',
  description: 'litgraph: run the edge-judge on every candidate batch, one agent per batch',
  whenToUse: 'Called by /litgraph:synthesize with args {project, plugin_root, batches: [...candidates index batches]}',
  phases: [
    { title: 'Judge', detail: 'one edge-judge per batch of candidate pairs' },
    { title: 'Repair', detail: 'second pass for batches whose output did not pass check-judgments' },
  ],
}

const RESULT = {
  type: 'object',
  properties: {
    batch_id: { type: 'string' },
    ok: { type: 'boolean', description: 'true if check-judgments reported ok:true at the end' },
    pairs: { type: 'integer' },
    edges: { type: 'integer' },
    notes: { type: 'string' },
  },
  required: ['batch_id', 'ok', 'pairs', 'edges', 'notes'],
}

const project = args.project
const schemaPath = `${args.plugin_root}/schemas/judgments.schema.json`
const batches = args.batches || []

function prompt(b) {
  return [
    `Judge one batch of candidate finding pairs for the litgraph project at ${project}.`,
    `batch file: ${b.path}`,
    `batch_id: ${b.batch_id} (${b.pairs} pairs)`,
    `out_path: ${b.out_path}`,
    `project folder (for litgraph -C): ${project}`,
    `JSON Schema: ${schemaPath}`,
    '',
    'Follow your instructions, write the judgments to out_path, run check-judgments until it passes, and report the result.',
  ].join('\n')
}

if (!batches.length) {
  log('No candidate batches to judge (all cached).')
  return { results: [], failed: [] }
}
log(`Judging ${batches.length} batch(es)`)

const results = await pipeline(
  batches,
  b => agent(prompt(b), { label: `judge:${b.batch_id}`, phase: 'Judge', agentType: 'litgraph:edge-judge', schema: RESULT }),
  (r, b) => (r && r.ok) ? r
    : agent(`${prompt(b)}\n\nA first attempt did not pass check-judgments${r ? `: ${r.notes}` : ''}. If ${b.out_path} exists, fix it rather than starting over.`,
      { label: `repair:${b.batch_id}`, phase: 'Repair', agentType: 'litgraph:edge-judge', schema: RESULT }),
)

const failed = batches.filter((b, i) => !results[i] || !results[i].ok).map(b => b.batch_id)
if (failed.length) log(`Still failing after repair: ${failed.join(', ')}`)
return { results: results.filter(Boolean), failed }
