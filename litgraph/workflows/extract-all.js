export const meta = {
  name: 'extract-all',
  description: 'litgraph: run the paper-extractor on every paper that needs extraction, one agent per paper',
  whenToUse: 'Called by /litgraph:synthesize with args {project, plugin_root, papers: [...todo.json papers]}',
  phases: [
    { title: 'Extract', detail: 'one paper-extractor per paper, in parallel' },
    { title: 'Repair', detail: 'second pass for papers whose extraction did not pass its checks' },
  ],
}

const RESULT = {
  type: 'object',
  properties: {
    paper_id: { type: 'string' },
    ok: { type: 'boolean', description: 'true if check-extraction reported ok:true at the end' },
    findings: { type: 'integer' },
    notes: { type: 'string', description: 'one line: anything the orchestrator must know' },
  },
  required: ['paper_id', 'ok', 'findings', 'notes'],
}

const project = args.project
const schemaPath = `${args.plugin_root}/schemas/extraction.schema.json`
const papers = args.papers || []

function prompt(p) {
  return [
    `Extract the findings from one paper for the litgraph project at ${project}.`,
    `paper_id: ${p.paper_id}`,
    `pdf_path: ${p.pdf_path}`,
    `text_path: ${p.text_path || 'null (no text layer extracted; read the PDF directly)'}`,
    `file_hash: ${p.file_hash}`,
    `n_pages: ${p.n_pages ?? 'unknown'}`,
    `out_path: ${p.out_path}`,
    `project folder (for litgraph -C): ${project}`,
    `JSON Schema: ${schemaPath}`,
    '',
    'Follow your instructions, write the extraction to out_path, run check-extraction until it passes, and report the result.',
  ].join('\n')
}

function repairPrompt(p, first) {
  return [
    prompt(p),
    '',
    `A first attempt at this paper did not pass its checks${first ? `: ${first.notes}` : ' (the agent did not finish)'}.`,
    `If ${p.out_path} exists, start from it: run check-extraction, fix every error and quote problem, and re-run until ok.`,
  ].join('\n')
}

if (!papers.length) {
  log('No papers need extraction (all cached).')
  return { results: [], failed: [] }
}
log(`Extracting ${papers.length} paper(s)`)

const results = await pipeline(
  papers,
  p => agent(prompt(p), { label: `extract:${p.paper_id}`, phase: 'Extract', agentType: 'litgraph:paper-extractor', schema: RESULT }),
  (r, p) => (r && r.ok) ? r
    : agent(repairPrompt(p, r), { label: `repair:${p.paper_id}`, phase: 'Repair', agentType: 'litgraph:paper-extractor', schema: RESULT }),
)

const done = results.filter(Boolean)
const failed = papers.filter((p, i) => !results[i] || !results[i].ok).map(p => p.paper_id)
if (failed.length) log(`Still failing after repair: ${failed.join(', ')}`)
return { results: done, failed }
