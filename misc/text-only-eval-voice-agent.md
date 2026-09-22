Text-In/Text-Out Task Evaluation
Proposed design for adding text-based task evaluation on top of the existing EVA pipeline in https://gitlab-master.nvidia.com/jarvis/voice-agent-evaluation


Overview
The current voice evaluation runs a simulated customer and an agent under test through an audio conversation. The proposed text mode keeps the same task, prompts, tools, database, and scoring, but exchanges text directly between the two agents.
The purpose is to measure conversation handling, reasoning, tool use, and task completion without ASR, TTS, voice activity detection, or audio transport affecting the result.
Goals
* Reuse the existing EVA scenarios, caller behavior, tools, expected database, scorer, and run artifacts.
* Support multi-turn text conversations with protocol-correct tool calls.
* Keep voice evaluation unchanged and report text results separately.
Not evaluated in text mode
* Speech recognition, speech generation, pronunciation, noise handling, interruptions, or voice turn detection.
* Voice latency such as time from end of speech to first audio.
Existing EVA foundation
The EVA airline implementation in the repo already provides most of the required text pipeline:
* The runner loads scenarios, starts sessions, and saves transcripts, tool calls, scores, and event logs.
* The simulated caller uses a text LLM and maintains its own conversation history.
* The Realtime session already sends caller messages as input_text items.
* The tool executor runs EVA airline tools against a private database for each scenario.
* The scorer compares the final database with the expected database and checks authentication and tool validity.
The main limitation is that the current session can still receive audio-output transcripts. It must explicitly request text-only output.
Proposed architecture
Add a direct text evaluation path beside the existing voice path. Text mode should not pass through the Pipecat audio pipeline, RTVI audio bridge, or Realtime audio proxy.
Architecture diagram


High-level flow:
* [NEW] A dedicated TextEvaluationRunner receives a normalized scenario from the reused EVA loader through the new scenario adapter and owns the text turn loop.
* [NEW] A dedicated TextCallerAgent uses Chat Completions to produce customer text. It reuses the EVA caller prompt, private goal, history rules, and completion logic, but not the voice bot, ASR, or TTS.
* [NEW] A RealtimeTextClient sends the caller message to the deployed agent through the OpenAI Realtime WebSocket.
* [REUSED] Existing domain tools and the scenario database execute locally; the text client returns each result as a function_call_output item.
* [REUSED] The same deployed agent, system prompt, and tool schemas produce the response. No server-side model change is expected.
* The conversation repeats until the caller appends the plain-text <END> marker, the optional EndConversationTool is called, or a configurable timeout or turn limit is reached.
* [REUSED] The existing scorer runs unchanged on the final state, transcript, tool calls, and termination reason.
Design decision
Add a dedicated TextEvaluationRunner instead of reusing or converting the voice bridge. The new runner handles only text turns; the existing voice runner remains unchanged. Both runners reuse the same scenario, tool, scoring, and artifact layers.
Required changes
1. Realtime text session
* Configure the deployed agent with output_modalities set to text.
* Send customer messages using conversation.item.create with input_text, followed by response.create.
* Collect response.output_text events and use response.done as the end of one agent turn.
* Return tool results using function_call_output, then ask the agent to continue with response.create.
The deployed agent keeps its conversation history inside the Realtime session.
OpenAI Realtime text payloads
No deployed-server code change is expected. The same Realtime endpoint should work out of the box when the client sends the standard text payloads below. Endpoint compatibility and the tool-result loop must still be confirmed with a smoke test.
Configure text-only output:
{
  "type": "session.update",
  "session": {
    "output_modalities": ["text"],
    "instructions": "...",
    "tools": [...],
    "tool_choice": "auto"
  }
}
Send caller text and request a response:
{
  "type": "conversation.item.create",
  "item": {
    "type": "message",
    "role": "user",
    "content": [{"type": "input_text", "text": "..."}]
  }
}
{"type": "response.create"}
Read response.output_text.delta events until response.done.
2. Caller agent
* Create a dedicated TextCallerAgent using the NVIDIA Inference Hub Chat Completions client. Reuse the prompt-building, private-goal, history, and completion logic from EVA SimulatedCaller.
* Build its private prompt from the scenario identity, goal, facts, constraints, and completion rules.
* Keep caller history separate from the deployed agent history.
* Instruct the text caller to append <END> when its private goal is complete or impossible. The marker is part of the caller's generated text, not a tool call.
3. Scenario and prompts
* Add a text prompt addendum and stop adding instructions that only make sense for spoken output.
* Introduce a common scenario representation for caller prompt, agent prompt, tools, initial state, expected state, and scoring rules.
* Add adapters that convert EVA and later Labs or Tau2 scenarios into that common representation.
The expected outcome remains private to the evaluator and is never shown to either agent.
4. Tools and state
* Keep tool execution inside the evaluator so every scenario has an isolated database.
* Generalize the EVA tool executor into an interface that can execute agent-side and, when needed, caller-side tools.
* Add Tau2 telecom later because it requires separate user and agent databases plus state synchronization.
5. Termination and failures
* End an agent turn on response.done.
* <END> is a plain-text marker generated by the TextCallerAgent when its private goal is complete or impossible. Example: "Thank you, that completes my request. <END>" It is not an OpenAI tool call.
* The TextEvaluationRunner detects and strips <END>, records caller_ended_call, and stops the text conversation. The marker itself is not forwarded to the deployed agent.
* The Labs voice pipeline uses a separate EndConversationTool called by the tested agent. Text mode may support that tool when clean-exit parity is required.
* Maximum turns, timeout, and endpoint-error limits remain configurable fallback termination conditions.
* Record the exact termination reason in the scenario result.
The normal function_call_output path is required for comparable results. The existing cancel-and-inject workaround may be retained for debugging, but runs using it must remain marked as degraded.
6. Scoring and artifacts
* Reuse the existing scorer unchanged for database matching, authentication, tool validity, natural-language assertions, and clean-exit checks. Only the source of the transcript changes from audio-derived text to direct text.
* Continue saving transcript, tool calls, event log, final database hashes, per-scenario score, and aggregate score.
* Record mode as text, with ASR and TTS marked as not under test.
* Measure time to first text token, response completion time, tool time, and total scenario duration.
Implementation plan
* Phase 1: make the existing EVA airline session truly text-only and validate the normal tool-result channel - this will be a quick POC.
* Phase 2: extract a generic text session, scenario model, and tool-executor interface/adaptor from the EVA implementation and support TAU2.
* Phase 3: add a Labs scenario adapter, followed by Tau2 airline and retail support.
* Phase 4: add Tau2 telecom state synchronization and expose a unified modality option for text or voice.
Code references
* EVA runner - scripts/run_eva_airline.py
* Realtime session - src/voice_agent_eval/benchmarks/eva/session.py
* Caller agent - src/voice_agent_eval/benchmarks/eva/caller.py
* Scenario loader - src/voice_agent_eval/benchmarks/eva/dataset.py
* Tool executor - src/voice_agent_eval/benchmarks/eva/tools.py
* Scorer - src/voice_agent_eval/benchmarks/eva/scorer.py