# AI Agent function contract — v2

Current desktop contract, including Measurement Workflow v2. Bundled
`agent_knowledge.py` supplies help even outside the repository; this file supplements
it. Executable capabilities and schemas come from `build_agent_capabilities(context)`.

## Role and routing

Reply in the user's language. Explain existing features and coordinate high-level
workflows. Do not turn a how-to question into a hardware action.

- Help/inspect: answer from supplied knowledge and state, with an empty steps array.
- Execute: return ordered, explicit, supported actions. Local guards and the user's
  selected execution mode remain authoritative.
- Ambiguous/unsupported: ask a focused question, set `needs_clarification=true`.
  Do not execute just the supported fragment of an ambiguous request.

Deterministic commands run before provider routing and require no API key:

| Prompt | Behavior |
| --- | --- |
| `/help [overview/workflow/mapping/instruments/imaging]` | Current-version help; no execution. |
| `/status` | Current state and capability availability; no execution. |
| `/check` | Local measurement graph/geometry report; no device connection or output. |
| `/open AutoTest` | Plan navigation to an existing page. |
| `/run measurement` | Review/run the currently configured Probe or Wire-bond workflow. |

## Input

`AgentPlanner.plan(instruction: str, context: AgentContext) -> AgentPlan`

`AgentContext.to_prompt_dict()` includes `contract_version=2`, stage/camera/GDS state,
active workflows, probe contact, page, image output, measurement mode, summary,
blockers and fingerprint. It does not contain the API key. Instrument connectivity
is explicitly **not verified** by graph inspection.

The provider receives `capabilities` with `parameters_schema`, prerequisites,
effects and availability; a Markdown brief; and current bundled help. Tk state is
collected on the main thread before network planning. Bounded history includes
completed tool results; the local fallback interprets only the current request.

## Provider response

Return one JSON object without Markdown fences:

```json
{
  "title": "Focus and capture",
  "understanding": "Use current AutoFocus settings, then save one frame.",
  "reply_markdown": "Review the steps before running them.",
  "needs_clarification": false,
  "plan": {
    "steps": [
      {"action_id": "autofocus_current_position", "title": "AutoFocus", "module": "AutoFocus", "detail": "Use current settings.", "parameters": {}},
      {"action_id": "capture_single_frame", "title": "Save frame", "module": "ImgStitch", "detail": "Capture after autofocus finishes.", "parameters": {}}
    ]
  },
  "blockers": [],
  "recovery_suggestions": []
}
```

For help, use `"plan":{"steps":[]}` and put the answer in `reply_markdown`.
Optional display fields include `visualization_hint`, `risks` and recovery suggestions.
Local permission/effect flags cannot be lowered by provider output. Unknown actions
become clarification and prevent execution. Prose is never executed.

## Actions

| action_id | Parameters | Effect |
| --- | --- | --- |
| `help` | optional `topic` | Bundled help. |
| `status_summary` | `{}` | Current state. |
| `check_measurement_workflow` | `{}` | Local inspection. |
| `open_page` | `{"page":"AutoTest"}` | Existing `show_page`; no hardware change. |
| `stage_move` | See below | Existing coordinate worker. |
| `move_gds_target` | `{}` | Existing selected GDS target move. |
| `autofocus_current_position` | `{}` | Existing AutoFocus. |
| `capture_single_frame` | `{}` | Save a unique single-frame file and update the latest image without clearing or overwriting the current ImgStitch session, stack result, or tiles. |
| `image_capture_sequence` | `{}` | Current ImgStitch / T-stack / Z-stack settings. |
| `focusmap_current_settings` | `{}` | Existing generated mesh/settings. |
| `layout_image_overlay` | `{}` | Associate latest image path/open LayoutBond; not automatic registration. |
| `run_current_measurement` | `{"fingerprint":"<current context digest>"}` | `start_autotest`, routing to Probe or Wire-bond. |
| `clarify` | `{}` | Ask a question; not executable. |

Navigation pages: Main, AutoFocus, FocusMap, LayoutBond, ImgStitch, ImgMatrix,
AutoTest, EdgeTrace, AI Agent, Communication, Config.

`open_page` also accepts the user-facing compatibility aliases `MainView`,
`LayoutMap`, `SerialIO`, and `Settings`; they resolve to `Main`, `LayoutBond`,
`Communication`, and `Config` before navigation. Responses use the current visible
page name.

Canonical stage shapes:

```json
{"mode":"zero","axes":["X","Y"]}
{"mode":"absolute","targets":{"X":100,"Y":0}}
{"mode":"relative","deltas":{"X":10,"Y":-5}}
```

Use integer controller **pulses** within signed 32-bit range. Axes/values must match
when both are supplied. Non-finite, fractional, Boolean, unknown-axis and extra
parameters are rejected. `zero` moves to coordinate zero; it is not limit-switch
homing or redefining the origin. Physical-unit conversion is not an Agent action.
Final controller/motion constraints still apply.

## Current measurement features and limits

Help covers shared Workflow v2, control/data links, formulas, result ports, Plot Panel,
Probe versus Wire-bond, instrument templates, DP100, TEMT6000 and Virtual Mask.
Measurement execution uses the current editor's configured nodes/parameters.
Use the existing editor for template loading and instrument limit changes.
The Agent does not freely write graphs or independently change instrument limits.

Graph validation rejects invalid connections and unimplemented placeholders.
Wire-bond additionally applies its existing supported-node/parameter checks.
Local inspection passing does not guarantee device reachability or acquisition success.
ImgMatrix, EdgeTrace and manual instrument controls are help/navigation-only unless
an explicit action is registered.

Never emit raw controller commands, Python, arbitrary file writes or unregistered
instrument operations. Do not change safety flags to force execution. Stop on failure
or cancellation; report actual results and recovery options. A provider response or
progress string is not completion evidence.

See [execution contract](agent-execution-spec.md) and [test templates](agent-test-templates.md).
