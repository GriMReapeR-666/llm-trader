"""PU-0112: craft a START_DECK.bat patch containing ONLY the QKF preflight block.

Reads artifacts/pu_bat_full.diff (git diff output + PU footer lines), keeps
the context around my preflight insertion plus only my added lines, drops
the user's hunks (args echo, boot-mode selection, trader autostart, TUI_ARGS)
and the user's boot-mode additions inside the shared hunk, then recomputes
hunk headers.  Output must apply cleanly against HEAD (verify with
git apply --check before staging).
"""
SRC = "artifacts/pu_bat_full.diff"
DST = "artifacts/pu_deck_preflight.patch"

with open(SRC, encoding="utf-8", errors="replace") as fh:
    raw = fh.read().splitlines()

# Strip git warnings and the PU probe footer.
lines = [ln for ln in raw if not ln.startswith("warning:")]
while lines and (lines[-1].startswith("exit=") or lines[-1].startswith("END-MARKER")):
    lines.pop()

# Isolate the START_DECK.bat file section.
start = next(i for i, ln in enumerate(lines) if ln.startswith("diff --git a/START_DECK.bat"))
end = next((i for i, ln in enumerate(lines) if ln.startswith("diff --git a/START_TRADING.bat")), len(lines))
section = lines[start:end]

header = section[0:4]  # diff --git / index / --- / +++
assert header[2].startswith("--- a/START_DECK.bat"), header
assert header[3].startswith("+++ b/START_DECK.bat"), header

# Split the section into hunks; keep the one holding my preflight additions.
hunk_starts = [i for i, ln in enumerate(section) if ln.startswith("@@")]
hunk_starts.append(len(section))
target = None
for a, b in zip(hunk_starts, hunk_starts[1:]):
    if any("+REM -- Optional QKF smoke preflight" in ln for ln in section[a:b]):
        target = section[a:b]
        break
assert target is not None, "preflight hunk not found"

body = target[1:]  # drop the @@ line; headers stay from the file section

# Leading context: 3 lines (tail of the user's python-check if-block).
ctx_a = body[:3]
assert ctx_a[0].strip() == "exit /b 1" and ctx_a[1].strip() == ")", ctx_a

added = []
i = 3
while i < len(body) and body[i].startswith("+"):
    added.append(body[i])
    i += 1
assert added[0].startswith("+REM -- Optional QKF smoke preflight"), added[0]
assert added[-1] == "+", added[-1:]  # trailing blank added line

# Trailing context: the 6-line if-not-exist guard group (5 + blank) that
# precedes the user's boot-mode additions in the working copy.
ctx_b = body[i:i + 6]
assert ctx_b[0].startswith(" if not exist"), ctx_b[0]
assert ctx_b[5].strip() == "", ctx_b[-1:]

new_body = ctx_a + added + ctx_b
old_count = sum(1 for ln in new_body if ln.startswith(" "))
new_count = old_count + sum(1 for ln in new_body if ln.startswith("+"))
# Old-side start line: 3 context lines above the insertion sit at old 35-37.
hunk = f"@@ -35,{old_count} +35,{new_count} @@"

with open(DST, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(header + [hunk] + new_body) + "\n")

print(f"wrote {DST}: {len(new_body)} body lines, old={old_count} new={new_count}")
print("PU-0112-OK")
