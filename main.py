"""Agent loop.

Runs forever: each turn loads prior VLM output (the story), executes
actions via execute.py, captures a screenshot, sends everything to the
VLM, and stores the raw response as the new story (SST rule).

Reads FRANZ_RUN_DIR from environment (set by panel.py) to locate all
per-execution artifacts. Falls back to a timestamped subdirectory under
panel_log/ if not set.
"""

import importlib
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Final

import config as franz_config

API: Final = "http://localhost:1234/v1/chat/completions"
MODEL: Final = "qwen3-vl-2b-instruct-1m"
WIDTH: Final = 512
HEIGHT: Final = 288
VISUAL_MARKS: Final = True
LOOP_DELAY: Final = 0.01
EXECUTE_ACTIONS: Final = True
SANDBOX: Final = True
PHYSICAL_EXECUTION: Final = False
EXECUTE_SCRIPT: Final = Path(__file__).parent / "execute.py"

RUN_DIR: Final = Path(os.environ.get("FRANZ_RUN_DIR", ""))
if not RUN_DIR.is_dir():
    _fb = Path(__file__).parent / "panel_log" / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    _fb.mkdir(parents=True, exist_ok=True)
    RUN_DIR_RESOLVED: Final = _fb
else:
    RUN_DIR_RESOLVED: Final = RUN_DIR  # type: ignore[misc]

STATE_FILE: Final = RUN_DIR_RESOLVED / "state.json"

SYSTEM_PROMPT: Final = """\
You are a drawing agent. You run in a loop. Each turn:
1. You read what you wrote last time. That is your ONLY memory.
2. You see a screenshot of the screen.
3. You see execution feedback showing what worked or failed.

Then you write your next output. What you write now REPLACES your memory.

YOUR OUTPUT FORMAT -- you MUST use this exact structure every turn:

RULES I KNOW:
(List 3-7 rules you have discovered about how tools work and how to draw well. \
Keep rules that are still true. Replace wrong rules with corrected ones. \
If you have no rules yet, write your best guesses.)

WHAT I SEE:
(Describe in 1-2 sentences what is currently on the screen. \
Mention white shapes you drew and where they are. Be specific about coordinates.)

MY DRAWING PROGRESS:
(List which parts of the cat are done and which remain. Example: \
"Head outline: done. Left ear: done. Right ear: not started. Eyes: not started.")

NEXT STEP:
(Write ONE specific action you will do now. Be precise. Example: \
"Draw the right ear using 3 drags from (600,250) to (650,200) to (700,250).")

```python
(your tool calls here)
```

Coordinates: 0 to 1000. Top-left is (0,0). Bottom-right is (1000,1000).
The CENTER of the screen is (500, 500). Draw your cat near the center.

CRITICAL RULES:

1. Write EXACTLY ONE ```python block. Only the FIRST block executes. \
If you write multiple blocks, everything after the first is IGNORED.

2. drag() draws a STRAIGHT LINE between two points. It does NOT draw curves. \
To make a curve, use 6-12 drags in a row with gradually changing coordinates.

3. type() only works after left_click(). Click first to set cursor position.

4. Keep your ```python block SHORT: 3-12 tool calls per turn. \
Do not try to draw everything at once. Build piece by piece.

5. Do NOT write "Wait", "Let me reconsider", or hesitate. Commit to your plan and execute it.

6. Use coordinates in the 300-700 range so your drawing is visible and centered.

7. If feedback says "no block found", you forgot the ```python block. Add one.

READING THE SCREENSHOT:
The red cursor arrow shows where your cursor IS NOW after your last actions.
The faded red cursor arrow shows where your cursor WAS BEFORE your last actions.
Both cursors display their normalized (0-1000) coordinates.
White shapes on the black canvas are PERMANENT drawings from ALL turns.
Use the cursor positions to verify your last actions landed correctly.

YOUR TASK:
Draw a picture of a cat on the black screen. The cat should have:
a round head, two pointed ears, eyes, nose, mouth, whiskers, and a body.
Use drag() for lines and curves. Use left_click() for dots.
Work step by step across many turns. Draw one part per turn.

EXAMPLE of a good first output:

RULES I KNOW:
1. drag() draws a straight white line between two points.
2. To make a circle, I need many drags arranged in a ring pattern.
3. Coordinates go from 0 to 1000. Center of screen is (500, 500).
4. I should use 8-12 short drags to approximate a curve.
5. left_click() makes a white dot, good for eyes and nose.

WHAT I SEE:
Black empty screen with a timestamp at the bottom.

MY DRAWING PROGRESS:
Head outline: not started. Ears: not started. Eyes: not started. \
Nose: not started. Mouth: not started. Whiskers: not started. Body: not started.

NEXT STEP:
Draw the top half of the cat's head as a semicircle centered at (500, 350) \
with radius about 150, using 8 short drags.

```python
drag(350, 350, 370, 290)
drag(370, 290, 410, 240)
drag(410, 240, 460, 210)
drag(460, 210, 500, 200)
drag(500, 200, 540, 210)
drag(540, 210, 590, 240)
drag(590, 240, 630, 290)
drag(630, 290, 650, 350)
```\
""".strip()

TOOLS_ENABLED: Final = {
    "left_click": True, "right_click": True, "double_left_click": True,
    "drag": True, "type": True, "screenshot": True, "click": True,
}


def _load_state() -> tuple[str, int]:
    try:
        o = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(o, dict):
            return str(o.get("story", "")), int(o.get("turn", 0))
    except Exception:
        pass
    return "", 0


def _save_state(turn: int, story: str, prev_story: str, raw: str, er: dict[str, object]) -> None:
    try:
        STATE_FILE.write_text(json.dumps({
            "turn": turn, "story": story, "prev_story": prev_story,
            "vlm_raw": raw,
            "executed": er.get("executed", []), "malformed": er.get("malformed", []),
            "ignored": er.get("ignored", []), "wants_screenshot": er.get("wants_screenshot", False),
            "execute_actions": EXECUTE_ACTIONS, "tools": TOOLS_ENABLED,
            "timestamp": datetime.now().isoformat(),
        }, indent=2), encoding="utf-8")
    except Exception:
        pass


def _sampling_dict() -> dict[str, float | int]:
    return {
        "temperature": float(franz_config.TEMPERATURE),
        "top_p": float(franz_config.TOP_P),
        "max_tokens": int(franz_config.MAX_TOKENS),
    }


def _infer(screenshot_b64: str, prev_story: str, feedback: str) -> str:
    payload: dict[str, object] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": prev_story}]},
            {"role": "user", "content": [
                {"type": "text", "text": feedback},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ]},
        ],
        **_sampling_dict(),
    }
    body_bytes = json.dumps(payload).encode()
    req = urllib.request.Request(API, body_bytes, {"Content-Type": "application/json"})
    delay = 0.5
    last_err: Exception | None = None
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                return json.load(resp)["choices"][0]["message"]["content"]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(delay)
            delay = min(delay * 2.0, 8.0)
    raise RuntimeError(f"VLM request failed after retries: {last_err}")


def _run_executor(raw: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(EXECUTE_SCRIPT)],
        input=json.dumps({
            "raw": raw, "tools": TOOLS_ENABLED, "execute": EXECUTE_ACTIONS,
            "physical_execution": PHYSICAL_EXECUTION, "sandbox": SANDBOX,
            "run_dir": str(RUN_DIR_RESOLVED),
            "width": WIDTH, "height": HEIGHT, "marks": VISUAL_MARKS,
        }),
        capture_output=True, text=True,
    )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def main() -> None:
    story, turn = _load_state()
    while True:
        turn += 1
        try:
            importlib.reload(franz_config)
        except Exception:
            pass
        prev_story = story
        er = _run_executor(prev_story)
        screenshot_b64 = str(er.get("screenshot_b64", ""))
        feedback = (str(er["feedback"]) if "feedback" in er
                    else "RuntimeError: executor subprocess failed. Retrying next turn.")
        raw = _infer(screenshot_b64, prev_story, feedback)
        story = raw
        _save_state(turn, story, prev_story, raw, er)
        time.sleep(LOOP_DELAY)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)