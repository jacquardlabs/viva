#!/usr/bin/env bash
set -euo pipefail

SKILLS_DIR="$HOME/.claude/skills"
VIVA_DIR="$SKILLS_DIR/viva"
TARGET_DIR="$SKILLS_DIR/brainstorming"
TARGET="$TARGET_DIR/SKILL.md"
VERSION_FILE="$VIVA_DIR/.brainstorm-patch-version"

# Locate the upstream superpowers brainstorming SKILL.md. Do NOT hard-code the
# marketplace/org cache dir — it has changed before and varies by install source.
# Search every plausible root for a `*superpowers*/…/brainstorming/SKILL.md`,
# newest version wins, and exclude our own patched copy ($TARGET) so a re-run
# never patches an already-patched file.
SEARCH_ROOTS=(
  "$HOME/.claude/plugins/cache"
  "$HOME/.claude/plugins"
  "$HOME/.claude/skills"
)

BRAINSTORM_SRC=""
for root in "${SEARCH_ROOTS[@]}"; do
  [[ -d "$root" ]] || continue
  BRAINSTORM_SRC=$(find "$root" -path "*/brainstorming/SKILL.md" 2>/dev/null \
    | grep -i superpowers \
    | grep -vxF "$TARGET" \
    | sort -t/ -V | tail -1 || true)
  [[ -n "$BRAINSTORM_SRC" ]] && break
done

if [[ -z "$BRAINSTORM_SRC" ]]; then
  echo "✗ Could not find the superpowers brainstorming skill." >&2
  echo "  Looked under:" >&2
  printf '    %s\n' "${SEARCH_ROOTS[@]}" >&2
  echo "  Install superpowers (it provides the brainstorming skill), then re-run:" >&2
  echo "  ! bash ~/.claude/skills/viva/scripts/install.sh" >&2
  exit 1
fi

VERSION=$(echo "$BRAINSTORM_SRC" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "✓ Found brainstorming skill (superpowers $VERSION)"

mkdir -p "$TARGET_DIR"
cp "$BRAINSTORM_SRC" "$TARGET"

# Remove previous injection if re-running (idempotent)
python3 - "$TARGET" << 'PYEOF'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
content = p.read_text()
content = re.sub(r'\n## Batch Q&A Phase.*?(?=\n## |\Z)', '', content, flags=re.DOTALL)
p.write_text(content)
PYEOF

# Build patch — Q&A phase instructions injected into brainstorming
PATCH='

## Batch Q&A Phase

When open questions remain after initial exploration, collect all answers in one browser session using viva'"'"'s Q&A mode. This replaces one-at-a-time terminal questions with structured, parseable responses (chip choices the agent can read exactly, with optional free-text for nuance).

**Version check:** At session start, run:
```bash
INSTALLED=$(cat ~/.claude/skills/viva/.brainstorm-patch-version 2>/dev/null || echo "unknown")
CURRENT=$(find ~/.claude/plugins ~/.claude/skills -path "*/brainstorming/SKILL.md" 2>/dev/null | grep -i superpowers | sort -t/ -V | tail -1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" | head -1 || echo "unknown")
[[ -n "$CURRENT" && "$INSTALLED" != "$CURRENT" ]] && echo "⚠ Brainstorming skill updated ($INSTALLED → $CURRENT). Re-run: ! bash ~/.claude/skills/viva/scripts/install.sh"
```

**When to use:** Generate batch Q&A when you have 3+ clarifying questions with discrete answers. For 1–2 quick questions, continue in-terminal.

**How to use:**

Generate all clarifying questions with 2–4 choices each. Write `.viva/qa-input.json`:

```json
{
  "mode": "qa",
  "context": "One-sentence description of what is being designed",
  "questions": [
    {
      "id": "q1",
      "text": "The question text",
      "hint": "Optional elaboration or framing",
      "choices": ["Choice A", "Choice B", "Choice C"]
    }
  ]
}
```

Launch the Q&A server:

```bash
python ~/.claude/skills/viva/server.py \
  --mode qa \
  --input .viva/qa-input.json \
  --output .viva/answers.json
```

Read `.viva/answers.json` after the server exits. If an answer carries an `attachments` array, `Read` each listed image path before incorporating that answer. Unanswered questions: use best judgment and proceed.
'

# Inject after the "Ask clarifying questions" checklist item
python3 - "$TARGET" "$PATCH" << 'PYEOF'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
patch = sys.argv[2]
content = p.read_text()
result = re.sub(
    r'(\n\d+\.\s+\*\*Ask clarifying questions\*\*[^\n]*)',
    r'\1' + patch,
    content,
    count=1
)
if result == content:
    # Fallback: append at end if pattern not found
    result = content.rstrip() + '\n' + patch + '\n'
p.write_text(result)
PYEOF

echo "✓ Patched $TARGET"

echo "$VERSION" > "$VERSION_FILE"
echo "✓ Recorded patch version → .brainstorm-patch-version"
echo ""
echo "Brainstorming Q&A integration is active."
echo "Run this again after any superpowers update:"
echo "  ! bash ~/.claude/skills/viva/scripts/install.sh"
