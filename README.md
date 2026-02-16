```
# FRANZ -- Autonomous Drawing Agent with Self-Supervised Memory

FRANZ is an autonomous agentic system that drives a Vision-Language Model (VLM)
in a persistent loop, allowing it to draw on a virtual canvas using tool calls
expressed as Python code. The agent has no external memory -- its only continuity
across turns is the raw text it wrote on the previous turn, fed back verbatim as
input. A transparent reverse proxy observes all traffic, verifies memory integrity,
logs every turn, and streams live data to a four-quadrant browser dashboard.

The system runs on Windows 11 with Python 3.13 and has zero image-library
dependencies. All rendering -- sandbox canvas, visual marks, cursor icons,
bitmap font, PNG encoding -- is implemented from raw pixel manipulation and
Win32 GDI calls.

---

## Architecture Overview

```
+---------------------------------------------------------------------+
|                         panel.py (entry point)                      |
|                                                                     |
|  +------------------+     +-----------------+     +---------------+ |
|  | Reverse Proxy    |     | SSE Dashboard   |     | main.py       | |
|  | :1234            |     | :8080           |     | Lifecycle Mgr | |
|  |                  |     |                 |     |               | |
|  | Intercepts all   |     | Serves panel.   |     | Auto-launch   | |
|  | VLM traffic      |     | html + SSE      |     | Auto-restart  | |
|  | Verifies SST     |     | stream to       |     | Env: RUN_DIR  | |
|  | Logs + screens   |     | 4-quadrant UI   |     |               | |
|  +--------+---------+     +-----------------+     +-------+-------+ |
|           |                                               |         |
+-----------+-----------------------------------------------+---------+
            |                                               |
            | HTTP POST                                     | subprocess
            | (forwarded byte-for-byte)                     |
            v                                               v
+---------------------+                        +-----------------------+
| Upstream VLM        |                        | main.py (agent loop)  |
| LM Studio :1235     |                        |                       |
| qwen3-vl-2b-instruct|                        | Load story -> Execute |
+---------------------+                        | -> Screenshot -> VLM  |
                                                | -> Store new story    |
                                                +-----------+-----------+
                                                            |
                                                            | subprocess (stdin/stdout JSON)
                                                            v
                                                +-----------+-----------+
                                                | execute.py            |
                                                |                       |
                                                | Extract ```python     |
                                                | block from VLM output |
                                                | exec() in sandbox     |
                                                | Record actions        |
                                                | Build feedback        |
                                                +-----------+-----------+
                                                            |
                                                            | subprocess (stdin/stdout JSON)
                                                            v
                                                +-----------+-----------+
                                                | capture.py            |
                                                |                       |
                                                | Persistent BMP canvas |
                                                | Apply white drawings  |
                                                | Overlay visual marks  |
                                                | Encode PNG, resize    |
                                                | Return base64         |
                                                +-----------------------+
```

---

## Simulated Multi-Turn Data Flow

The following diagram traces data through five turns of the agent loop,
showing how the SST (Self-Supervised Text) rule creates continuity, how
the feedback mechanism adapts to errors, and how the dashboard presents
each turn in its four-quadrant layout.

```
TURN 1
======
  main.py                execute.py              capture.py               VLM
  --------               ----------              ----------               ---
  story = ""
  |
  +-- run_executor("") ------>|
  |                           | raw=""
  |                           | no ```python block
  |                           | feedback:
  |                           |   "SyntaxError: no ```python block found..."
  |                           |   ""
  |                           |   "Available tools:"
  |                           |   "  left_click(x, y) -- white dot at position"
  |                           |   "  drag(x1, y1, x2, y2) -- straight white line..."
  |                           |   "  type(text) -- type text at last click position"
  |                           |   "  ..."
  |                           |
  |                           +-- run_capture([timestamp()]) -->|
  |                           |                                 | Load/create black canvas
  |                           |                                 | Render timestamp badge
  |                           |                                 | MARKS_CURSOR: no positions -> skip
  |                           |                                 | Encode PNG, resize to 512x288
  |                           |<-- { screenshot_b64, applied }--+
  |<-- { feedback, screenshot_b64 } --+
  |
  +-- _infer(screenshot, "", feedback) ---------------------------------->|
  |                                                                       |
  |   Messages sent to VLM:                                               |
  |     [0] system: SYSTEM_PROMPT                                         |
  |     [1] user:   "" (empty story)                                      |
  |     [2] user:   feedback_text + screenshot_image                      |
  |                                                                       |
  |<-- "RULES I KNOW:\n1. drag() draws...\n...\n```python\n..." ---------+
  |
  story = raw VLM output (verbatim)
  save_state(turn=1, story=story)

       panel.py proxy intercepts the request/response:
         Extract sst_text from message[1] -> ""
         _last_vlm_text = None -> SST check: "First observed turn" (OK)
         Store _last_vlm_text = response text
         Log turn, save turn_0001.png, broadcast SSE

       panel.html receives SSE event, displayTurn(0):
         +---------------------------+---------------------------+
         | TOP-LEFT: SST             | TOP-RIGHT: Feedback       |
         | (empty)                   | SyntaxError: no ```python |
         |                           | block found...            |
         | SST: OK                   |                           |
         | model: qwen3-vl-2b        | Available tools:          |
         | latency: 1200ms           |   drag(x1,y1,x2,y2) --   |
         | prompt_tok: 1850          |     straight white line   |
         |                           |   left_click(x, y) --    |
         |                           |     white dot at position |
         |                           |   type(text) -- type text |
         |                           |     at last click position|
         |                           |   ...                     |
         +===========================+===========================+
         | BOTTOM-LEFT: VLM Response | BOTTOM-RIGHT: Screenshot  |
         | RULES I KNOW:             |                           |
         | 1. drag() draws a         | +---------------------+  |
         |    straight white line     | |                     |  |
         | 2. To make a circle, I    | |   (black canvas)    |  |
         |    need many drags...      | |                     |  |
         | ...                        | | CAPTURED 2025-...   |  |
         | ```python                  | +---------------------+  |
         | drag(350, 350, 370, 290)   |  ~12 KB                  |
         | drag(370, 290, 410, 240)   |                           |
         | ...                        |                           |
         +---------------------------+---------------------------+


TURN 2
======
  main.py                execute.py              capture.py               VLM
  --------               ----------              ----------               ---
  prev_story = story (full VLM output from turn 1)
  |
  +-- run_executor(prev_story) -->|
  |                               | Extract ```python block:
  |                               |   drag(350, 350, 370, 290)
  |                               |   drag(370, 290, 410, 240)
  |                               |   ... (8 drags total)
  |                               |
  |                               | exec() in sandboxed namespace
  |                               | All 8 succeed
  |                               | feedback: "OK: 8 actions executed."
  |                               |
  |                               +-- run_capture([8 drags + timestamp()]) -->|
  |                               |                                           |
  |                               |     Sandbox canvas: apply 8 white lines   |
  |                               |     Save updated BMP                      |
  |                               |     sandbox_state: prev=(null), cur=(650,350)
  |                               |                                           |
  |                               |     MARKS_CURSOR:                         |
  |                               |       prev cursor: null -> skip           |
  |                               |       current cursor at (650,350)         |
  |                               |       with label "(650,350)" solid red    |
  |                               |                                           |
  |                               |     Resize to 512x288                     |
  |                               |<-- { screenshot_b64, applied } -----------+
  |<-- { feedback, screenshot_b64 } --+
  |
  +-- _infer(screenshot, prev_story, "OK: 8 actions executed.") ---------->|
  |                                                                        |
  |   Messages sent to VLM:                                                |
  |     [0] system: SYSTEM_PROMPT                                          |
  |     [1] user:   prev_story (exact turn 1 output)  <-- SST RULE        |
  |     [2] user:   "OK: 8 actions executed." + screenshot                 |
  |                                                                        |
  |<-- "RULES I KNOW:\n1. drag() works...\nWHAT I SEE:\nI see white..." --+
  |
  story = raw VLM output
  save_state(turn=2)

       panel.py: sst_text matches _last_vlm_text -> SST OK (487 chars)
       broadcast SSE

       panel.html: displayTurn(1):
         +---------------------------+---------------------------+
         | TOP-LEFT: SST             | TOP-RIGHT: Feedback       |
         | RULES I KNOW:             | OK: 8 actions executed.   |
         | 1. drag() draws a         |                           |
         |    straight white line...  |                           |
         | ...                        |                           |
         | ```python                  |                           |
         | drag(350, 350, 370, 290)   |                           |
         | ...                        |                           |
         |                           |                           |
         | SST: OK                   |                           |
         | latency: 1450ms           |                           |
         +===========================+===========================+
         | BOTTOM-LEFT: VLM Response | BOTTOM-RIGHT: Screenshot  |
         | RULES I KNOW:             |                           |
         | 1. drag() works well for  | +---------------------+  |
         |    straight lines...       | | white semicircle    |  |
         | WHAT I SEE:               | |  on black canvas    |  |
         | I see white curved lines  | |                     |  |
         |   forming top of head...  | |  red cursor arrow   |  |
         | MY DRAWING PROGRESS:      | |  at (650,350) with  |  |
         | Head top: done...         | |  coordinate label   |  |
         | ...                        | +---------------------+  |
         +---------------------------+---------------------------+


TURN 3 (error case)
======
  main.py                execute.py                                       VLM
  --------               ----------                                       ---
  prev_story = story (turn 2 output, which has a bad drag call)
  |
  +-- run_executor(prev_story) -->|
  |                               | Extract ```python block:
  |                               |   drag(350, 290, 410)     <-- MISSING ARG
  |                               |
  |                               | exec() raises TypeError
  |                               |
  |                               | _clean_exec_error() produces:
  |                               |   "  Line 1: drag(350, 290, 410)"
  |                               |   "TypeError: drag() missing 1 required..."
  |                               |
  |                               |   (NO file path leaked)
  |                               |   (NO "C:\Users\..." exposed)
  |                               |
  |                               | show_help = True, so append:
  |                               |   ""
  |                               |   "Available tools:"
  |                               |   "  drag(x1, y1, x2, y2) -- straight white line..."
  |                               |   "  left_click(x, y) -- white dot at position"
  |                               |   "  ..."
  |                               |
  |                               +-- run_capture([timestamp()]) -->|
  |                               |     canvas unchanged (no actions succeeded)
  |                               |     state: prev=(650,350), cur=(650,350)
  |                               |     MARKS_CURSOR: both at same position (overlapping)
  |                               |<--  base64 PNG
  |<---------- { feedback, screenshot_b64 } -+
  |
  +-- _infer(screenshot, prev_story, feedback) --------------------------->|
  |                                                                        |
  |   VLM sees its own code, the error, and the correct tool signatures.   |
  |   Self-corrects on next turn.                                          |
  |                                                                        |
  |<-- corrected output with drag(350, 290, 410, 240) --------------------+

       panel.html: displayTurn(2):
         +---------------------------+---------------------------+
         | TOP-LEFT: SST             | TOP-RIGHT: Feedback       |
         | (turn 2 output)           |   Line 1: drag(350,290,   |
         |                           |     410)                  |
         | SST: OK                   | TypeError: drag() missing |
         |                           |   1 required positional   |
         |                           |   argument: 'y2'          |
         |                           | 0 actions executed before |
         |                           |   error.                  |
         |                           |                           |
         |                           | Available tools:          |
         |                           |   drag(x1,y1,x2,y2) --   |
         |                           |     straight white line   |
         |                           |   left_click(x, y) -- ... |
         +===========================+===========================+
         | BOTTOM-LEFT: VLM Response | BOTTOM-RIGHT: Screenshot  |
         | RULES I KNOW:             |                           |
         | 1. drag() needs exactly   | +---------------------+  |
         |    4 arguments...          | | same canvas as       |  |
         | ...                        | |   before (no new     |  |
         | ```python                  | |   lines this turn)   |  |
         | drag(350, 290, 410, 240)   | |                      |  |
         | ...                        | | cursor at (650,350)  |  |
         |                           | +---------------------+  |
         +---------------------------+---------------------------+


TURN 4 (multiple blocks)
======
  execute.py detects 2 ```python blocks.
  Only the first executes (1 drag).
  feedback:
    "WARNING: 2 ```python blocks found. Only the first executed."
    "OK: 1 action executed."

  No tools list shown (code succeeded, just a warning).

       panel.html: displayTurn(3):
         Top-right shows the warning + OK message.
         Bottom-right shows canvas with new line + cursor moved.
         Faded previous cursor visible at old position.


TURN 5 (type without click)
======
  execute.py exec() succeeds: type("meow") recorded.
  capture.py sandbox_apply: no last_x/last_y -> type skipped.
  execute.py detects not_applied: type("meow").
  feedback:
    "RuntimeError: type(\"meow\") had no visible effect"
    "(type() requires a prior left_click() to set cursor position)"
    "0 actions executed."
    ""
    "Available tools:"
    "  left_click(x, y) -- white dot at position"
    "  type(text) -- type text at last click position"
    "  ..."

       panel.html: displayTurn(4):
         Top-right shows the runtime error + tools list.
         Bottom-right shows unchanged canvas.
```

---

## System Components

### config.py

Hot-reloadable parameters. main.py calls `importlib.reload()` every turn.

| Parameter        | Type  | Default | Purpose                                    |
|------------------|-------|---------|-------------------------------------------|
| `TEMPERATURE`    | float | 0.7     | VLM sampling temperature                   |
| `TOP_P`          | float | 0.9     | VLM nucleus sampling threshold             |
| `MAX_TOKENS`     | int   | 900     | Maximum response tokens                    |
| `RESTRICTED_EXEC`| bool  | True    | Strip `__builtins__` from exec namespace   |
| `MARKS_CLASSIC`  | bool  | True    | Numbered circles/arrows per action         |
| `MARKS_CURSOR`   | bool  | False   | Cursor icon with coordinates overlay       |

### panel.py

Entry point. Manages three concurrent services:

- **Reverse Proxy** (`:1234`) -- Intercepts every VLM request/response.
  Verifies SST integrity by comparing the story field in each request
  against the previous VLM response. Logs violations to stderr. Saves
  turn metadata to batched JSON files and screenshots to PNG.

- **SSE Dashboard** (`:8080`) -- Serves `panel.html` and streams turn
  data via Server-Sent Events. Supports up to 20 concurrent browser
  clients with keepalive.

- **main.py Lifecycle** -- Launches `main.py` after a 10-second startup
  delay. Auto-restarts on exit with a 3-second cooldown. Passes the
  run directory via `FRANZ_RUN_DIR` environment variable.

All per-execution artifacts are stored in a timestamped directory under
`panel_log/`.

### main.py

The agent loop. Each turn:

1. Load previous story from `state.json`
2. Call `execute.py` as subprocess (passes previous story)
3. Receive screenshot + feedback from executor
4. Send system prompt + previous story + feedback + screenshot to VLM
5. Store raw VLM response as new story
6. Save state and repeat

The system prompt enforces a structured output format:

```
RULES I KNOW:        (3-7 self-discovered rules)
WHAT I SEE:          (1-2 sentence screen description)
MY DRAWING PROGRESS: (checklist of cat parts)
NEXT STEP:           (one specific action plan)
```python             (tool calls)
```

Tool definitions are NOT in the system prompt. They are provided
dynamically through the feedback mechanism when errors occur.

### execute.py

Extracts and executes VLM code:

1. Parse ` ```python ` fenced blocks via regex
2. Detect multiple blocks (warn, execute only first)
3. `exec()` in a sandboxed namespace with tool functions
4. On error: `_clean_exec_error()` strips file paths, shows only
   the VLM's own code and the exception type
5. On any error: `_namespace_help()` appends tool documentation
   derived from function docstrings
6. Delegate to `capture.py` for screenshot production
7. Return structured JSON to main.py via stdout

The sandboxed namespace contains only tool functions. When
`RESTRICTED_EXEC` is true, `__builtins__` is set to `{}`,
preventing access to imports, open, eval, or any other Python
builtin.

Tool functions have docstrings that serve as the single source
of truth for documentation:

```
left_click(x, y) -- white dot at position
right_click(x, y) -- small white square at position
drag(x1, y1, x2, y2) -- straight white line from (x1,y1) to (x2,y2)
type(text) -- type text at last click position
screenshot() -- request a fresh screenshot
```

### capture.py

Screenshot production and rendering pipeline:

```
+------------------+     +------------------+     +------------------+
| Source           |     | Marks            |     | Output           |
|                  |     |                  |     |                  |
| Sandbox: load    |     | Classic: circles |     | Resize to target |
|   BMP canvas     |     |   arrows, nums   |     |   dimensions     |
|   Apply white    |     |                  |     |                  |
|   drawings       |     | Cursor: arrow    |     | Encode PNG       |
|                  |     |   icon + (x,y)   |     |   (hand-rolled)  |
| Desktop: GDI     |     |   labels, faded  |     |                  |
|   screen capture |     |   previous pos   |     | Base64 encode    |
|                  |     |                  |     |                  |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
         +-->  full-res RGBA  --->+-->  marked RGBA  ----->+-->  base64 PNG
```

**Sandbox Canvas**: Persistent BMP file at screen resolution. Each turn,
tool actions are rendered as permanent white drawings (lines, dots, rectangles,
text). State file tracks cursor position for `type()` dependency on prior
`left_click()`.

**Visual Marks -- Classic Mode** (`MARKS_CLASSIC=True`):
- Red filled circles with white numbers for `left_click`
- Red diamonds with numbers for `right_click`
- Red arrows with numbered start points for `drag`
- Red underlines for `type` actions
- Faint red trail lines connecting sequential actions
- Timestamp badge centered on canvas

**Visual Marks -- Cursor Mode** (`MARKS_CURSOR=True`):
- Red cursor arrow icon at current position after execution
- Faded (low alpha) red cursor arrow at previous position
- Both display normalized (0-1000) coordinate labels
- Label placement adapts to edges: shifts to visible quadrant
  when cursor is near screen boundaries
- Timestamp badge centered on canvas

Both modes can be enabled simultaneously or independently.

**Software Renderer**: Pure Python `Canvas` class operating on RGBA
byte buffers. Implements:
- Pixel-level alpha blending
- Bresenham line drawing with configurable thickness
- Filled circles, rectangles, polygon scanline fill
- Arrows with triangular heads
- 5x7 bitmap font (uppercase A-Z, digits 0-9, punctuation including
  parentheses for coordinate labels)
- Outlined number rendering for readability on any background

**PNG Encoder**: Hand-rolled -- constructs IHDR, IDAT (zlib compressed),
IEND chunks with CRC checksums. No dependency on PIL, Pillow, or any
image library.

### panel.html

Live dashboard served by panel.py on port 8080. Connects to `/events`
via Server-Sent Events and renders each turn in a four-quadrant layout
optimized for 16:9 displays.

**Quadrant Layout**:

```
+-------------------------------+-------------------------------+
| TOP-LEFT                      | TOP-RIGHT                     |
| SST -- Agent Memory           | Feedback -- Execution Result  |
| (previous turn output)        | (injected into VLM context)   |
|                               |                               |
| Contains: full SST text,      | Contains: feedback message    |
| SST verification badge,       | from execute.py, error        |
| metadata grid (model,         | details if present            |
| sampling params, token        |                               |
| usage, latency, sizes)        |                               |
+===============================+===============================+
| BOTTOM-LEFT                   | BOTTOM-RIGHT                  |
| VLM Response                  | Screenshot -- Pipeline Image  |
| (becomes next turn SST)       | (16:9 canvas with marks)      |
|                               |                               |
| Contains: raw VLM output      | Contains: base64 PNG from     |
| that will be fed back as      | capture.py rendered as img,   |
| agent memory next turn        | click to open full size       |
+-------------------------------+-------------------------------+
```

**Draggable Separators**: The central horizontal and vertical
separator lines can be dragged with the mouse to resize quadrants.
The vertical separator controls the left/right split. The horizontal
separator controls the top/bottom split. Both clamp between 15% and
85% to prevent unusably small quadrants.

**Turn Navigation**: Navigation buttons and keyboard shortcuts allow
browsing through all received turns:
- First / Previous / Next / Last buttons in the controls bar
- Arrow Left/Right keys, Home/End keys
- Auto-advance checkbox: when enabled, the display automatically
  jumps to each new turn as it arrives via SSE

**History Overlay**: The History button opens a full-screen overlay
listing all past turns with their SST, feedback, VLM response, and
screenshot sections. All turn cards in the history are expanded by
default, eliminating the need to manually expand each one when using
Save Page As for archival.

**Technical Details**:
- No external libraries, frameworks, or CDN resources
- Dark theme with CSS custom properties
- Color-coded quadrant headers: blue (SST), orange (feedback),
  green (VLM response), purple (screenshot)
- Monospace font for all data fields
- Double-click a history card header to jump to that turn in the
  quadrant view

---

## The SST Rule (Self-Supervised Text)

The core architectural principle. The VLM's raw output from turn N is
stored verbatim and sent back as input on turn N+1. This creates:

1. **Atemporal memory** -- The agent's only continuity is what it writes.
   There is no external state, no database, no conversation history beyond
   one turn. The agent must encode everything it needs into its own output.

2. **Integrity verification** -- The panel proxy compares the story field
   in each request against the last VLM response it observed. Any mismatch
   is logged as an SST violation with character-level diff details.

3. **Self-authoring instruction manual** -- The structured output format
   means the agent is effectively writing an instruction manual for its
   future self: what rules it has learned, what it sees, what progress
   it has made, and what to do next.

---

## Feedback Mechanism

The feedback system follows a uniform pipeline with no special cases:

```
+-------------------+     +-------------------+     +-------------------+
| Code Execution    |     | Error Detection   |     | Feedback Assembly |
|                   |     |                   |     |                   |
| exec() VLM code   |---->| Success?          |---->| "OK: N actions    |
| in sandbox        |     |   yes: count acts |     |  executed."       |
|                   |     |   no: clean trace  |     |                   |
|                   |     |                   |     | Error?             |
|                   |     | No block?          |     |   clean traceback |
|                   |     |   yes: flag it    |     |   + tools list    |
|                   |     |                   |     |                   |
|                   |     | Multiple blocks?   |     | Multi-block?      |
|                   |     |   yes: warn       |     |   warning line    |
+-------------------+     +-------------------+     +-------------------+
```

**Clean Tracebacks**: The `_clean_exec_error()` function filters Python
tracebacks to show only frames from `<string>` (the VLM's own code).
All internal file paths are stripped. The VLM sees:

```
  Line 1: drag(350, 290, 410)
TypeError: drag() missing 1 required positional argument: 'y2'
```

Instead of:

```
Traceback (most recent call last):
  File "C:\Users\username\projects\franz\execute.py", line 198, in main
    exec(code, ns)
  File "<string>", line 1, in <module>
TypeError: drag() missing 1 required positional argument: 'y2'
```

This eliminates sensitive data leakage (username, project path) and
reduces token waste.

**Dynamic Tool Discovery**: On any error, `_namespace_help()` iterates
the sandboxed namespace, reads `__doc__` from each callable, and appends
a tools listing to the feedback. This is the Python-native equivalent
of `dir()` -- the agent discovers its environment through the same
mechanism a developer would use in a REPL.

This design means:
- Tool definitions live in one place (function docstrings in execute.py)
- The system prompt does not need a TOOLS section
- Tool help appears only when relevant (on errors)
- Adding a new tool requires only adding the function with a docstring

---

## Data Flow Between Components

```
 panel.py                main.py              execute.py            capture.py
 (proxy+dashboard)       (agent loop)         (code executor)       (renderer)
    |                       |                      |                     |
    |                       | 1. load state.json   |                     |
    |                       |    story, turn        |                     |
    |                       |                      |                     |
    |                       | 2. subprocess call    |                     |
    |                       +---stdin JSON--------->|                     |
    |                       |   {raw, tools,       |                     |
    |                       |    sandbox, run_dir}  |                     |
    |                       |                      |                     |
    |                       |                      | 3. extract code     |
    |                       |                      |    exec() in ns     |
    |                       |                      |                     |
    |                       |                      | 4. subprocess call  |
    |                       |                      +--stdin JSON-------->|
    |                       |                      |  {actions, w, h,   |
    |                       |                      |   marks, sandbox,  |
    |                       |                      |   run_dir}         |
    |                       |                      |                     |
    |                       |                      |                     | 5. load BMP canvas
    |                       |                      |                     |    apply white draws
    |                       |                      |                     |    overlay marks
    |                       |                      |                     |    encode PNG
    |                       |                      |                     |
    |                       |                      |<--stdout JSON------+
    |                       |                      |  {screenshot_b64,  |
    |                       |                      |   applied}         |
    |                       |                      |                     |
    |                       |                      | 6. build feedback   |
    |                       |                      |    detect errors    |
    |                       |                      |    append tools if  |
    |                       |                      |    needed           |
    |                       |                      |                     |
    |                       |<--stdout JSON--------+                     |
    |                       |  {feedback,          |                     |
    |                       |   screenshot_b64,    |                     |
    |                       |   executed, ...}     |                     |
    |                       |                      |                     |
    |                       | 7. build VLM payload |                     |
    |                       |    [system, sst,     |                     |
    |                       |     feedback+image]  |                     |
    |                       |                      |                     |
    |  8. HTTP POST         |                      |                     |
    |<---VLM request--------+                      |                     |
    |                       |                      |                     |
    | 9. verify SST         |                      |                     |
    |    log turn           |                      |                     |
    |    save screenshot    |                      |                     |
    |    forward to :1235   |                      |                     |
    |    receive response   |                      |                     |
    |    broadcast SSE      |                      |                     |
    |                       |                      |                     |
    |---VLM response------->|                      |                     |
    |                       |                      |                     |
    |                       | 10. story = raw resp |                     |
    |                       |     save state.json  |                     |
    |                       |     loop to step 1   |                     |
    |                       |                      |                     |
    | 11. SSE -> browser    |                      |                     |
    |     panel.html:       |                      |                     |
    |     update quadrants  |                      |                     |
```

---

## File Structure

```
franz/
  config.py       Hot-reloadable sampling parameters and mark mode flags
  panel.py        Entry point: proxy, dashboard, logger, main.py lifecycle
  panel.html      Four-quadrant live dashboard with draggable separators
  main.py         Agent loop: story loading, VLM inference, state persistence
  execute.py      Code extraction, sandboxed execution, feedback construction
  capture.py      Sandbox canvas, visual marks, screenshot production
  panel_log/
    run_YYYYMMDD_HHMMSS/
      state.json            Current agent state
      sandbox_canvas.bmp    Persistent drawing surface
      sandbox_state.json    Cursor position tracking (current + previous)
      turn_0001.png         Screenshot per turn
      turns_0001_0015.json  Batched turn logs (15 turns per file)
```

---

## Requirements

- Windows 11
- Python 3.13
- LM Studio running a VLM on port 1235 (default: qwen3-vl-2b-instruct-1m)
- No pip dependencies (stdlib only, Win32 via ctypes)

## Running

```
python panel.py
```

This starts the proxy on `:1234`, the dashboard on `:8080`, and
auto-launches the agent loop after 10 seconds. Open
`http://127.0.0.1:8080/` in a browser to watch the agent draw.

To switch mark modes at runtime, edit `config.py`:

```python
MARKS_CLASSIC = False
MARKS_CURSOR = True
```

Changes take effect on the next turn (hot-reloaded).

## Dashboard Controls

| Control          | Action                                              |
|------------------|-----------------------------------------------------|
| Auto-advance     | Automatically display each new turn as it arrives   |
| Newest first     | Order turns newest-first in history overlay         |
| History          | Open full-screen overlay with all turns expanded    |
| Clear            | Remove all turns from display (does not affect logs)|
| << < > >>        | Navigate to first / previous / next / latest turn   |
| Arrow Left/Right | Keyboard navigation between turns                   |
| Home / End       | Jump to first / latest turn                         |
| Drag separators  | Resize quadrants by dragging the divider lines      |
```