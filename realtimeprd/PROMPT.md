@prd.md @activity.md

We are building the project according to the PRD in this repo.

First read activity.md to see what was recently accomplished.

## Start the Application

This project adds realtime voice modes (Gemini Live API + OpenAI Realtime API) to the Cheeko voice streaming pipeline in OpenClaw gateway.

**Start the OpenClaw gateway:**
```
cd openclaw && pnpm dev
```

**Run the Python voice client:**
```
uv run voice_client.py
```

**Prerequisites (must already be running):**
- OpenClaw gateway on port 18789 with cheeko stream enabled
- For pipeline mode: Deepgram API key (`DEEPGRAM_API_KEY`) + OpenAI/ElevenLabs API key
- For gemini mode: Gemini API key (`GEMINI_API_KEY`)
- For openai-realtime mode: OpenAI API key (`OPENAI_API_KEY`)
- Set mode via: `openclaw config set gateway.cheeko.mode gemini` (or `openai-realtime` or `pipeline`)

If a port is taken, try another port.

## Work on Tasks

Open prd.md and find the single highest priority task where `"passes": false`.

Work on exactly ONE task:
1. Implement the change according to the task steps
2. Run any available checks:
   - Gateway: `cd openclaw && pnpm build` (verify TypeScript compiles)
   - Client: `uv run python -c "import voice_client"` (verify client imports)
   - Gateway tests: `cd openclaw && pnpm test` (if applicable)
   - E2E tests: `uv run python test_e2e_cheeko.py` (pipeline mode regression)
   - Realtime tests: `uv run python test_realtime_gemini.py` or `uv run python test_realtime_openai.py`

## Verify in Browser

After implementing, use agent-browser to verify your work:

1. Open the local server URL:
   ```
   agent-browser open http://localhost:8080
   ```

2. Take a snapshot to see the page structure:
   ```
   agent-browser snapshot -i -c
   ```

3. Take a screenshot for visual verification:
   ```
   agent-browser screenshot screenshots/[task-name].png
   ```

4. Check for any console errors or layout issues

5. If the task involves interactive elements, test them:
   ```
   agent-browser click "[selector]"
   agent-browser fill "[selector]" "test value"
   ```

## Log Progress

Append a dated progress entry to activity.md describing:
- What you changed
- What commands you ran
- The screenshot filename
- Any issues encountered and how you resolved them

## Update Task Status

When the task is confirmed working, update that task's `"passes"` field in prd.md from `false` to `true`.

## Commit Changes

Make one git commit for that task only with a clear, descriptive message:
```
git add .
git commit -m "feat: [brief description of what was implemented]"
```

Do NOT run `git init`, do NOT change git remotes, and do NOT push.

## Important Rules

- ONLY work on a SINGLE task per iteration
- Always verify before marking a task as passing
- Always log your progress in activity.md
- Always commit after completing a task

## Completion

When ALL tasks have `"passes": true`, output:

<promise>COMPLETE</promise>
