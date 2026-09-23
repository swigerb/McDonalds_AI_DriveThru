# Order resume

A guest's order survives a short transport drop, such as a Wi-Fi blip, a load-balancer reset, a missed heartbeat, or a
backgrounded tab. When the browser reconnects within the hold, it gets the same session and order back, and the crew
member carries on without greeting again.

The protocol is implemented on both sides:

- backend: `rtmt.py` and `session_manager.py`;
- browser: `useRealtime.tsx` and `App.tsx`.

Resume covers the **cloud realtime path only**. See [Modes without resume](#modes-without-resume).

## What the guest sees

| Situation | What happens |
| --- | --- |
| Connection drops mid-order | The mic pauses and the status line reads "Connection dropped. Reconnecting — your order is safe…". The ticket stays on screen. |
| Reconnected within the hold | "Reconnected — your order is still here." The mic restarts without a tap. The crew member stays quiet until the guest speaks, and asks once after 30 s of silence whether they need anything else. |
| Browser won't restart the mic without a tap | "Reconnected — your order is still here. Tap the mic to continue." One tap continues the same order, with no greeting and no wait for one. |
| Guest taps the mic while it's still reconnecting | No "not connected" toast. The tap is held, and the mic opens as soon as the order comes back. |
| Page reloaded in the same tab | The ticket comes back with "Tap the mic to continue". |
| Hold expired, or the order couldn't be restored | "We couldn't restore your order. Tap the mic to start a new one." The ticket is cleared. |
| 5 minutes with no guest activity, connected or not | "Session ended after inactivity. Tap the mic to start a new order." It doesn't reconnect; the next tap starts fresh. |
| Same order resumed in another window | "This order continued in another window. Tap the mic to start again." |
| Guest presses **Start a new order** (shown once the ticket has items) | The server ends the order and the ticket clears. A fresh session is ready for the next tap. |

Resume is per tab (`sessionStorage`). A new tab or window always starts a new order. The notices are translated in
every UI locale (en, es, fr, ja).

## Modes without resume

| Mode | Resume? | Why |
| --- | --- | --- |
| Cloud realtime (default) | **Yes** | `ProcessorRouter` hands the socket to `RTMiddleTier._websocket_handler`, which owns the `SessionManager` grace hold. |
| Local mode (Phi-4 / Piper) | No | `ProcessorRouter` hands local sockets to `local_processor.handle_websocket`. It has no `SessionManager`, starts a new `local-…` session for every socket, and ignores `extension.*` frames. The browser sends no resume frame when `localMode` is on (`resumeActive` is false in `useRealtime`). |
| Azure Speech mode | No | Guest speech goes over HTTP (`/azurespeech/speech-to-text`), not the realtime socket. `App` passes `resumeEnabled: false` to `useRealTime`, so no `extension.resume` is sent and no resume id is stored. |

In local mode and Azure Speech mode, the UI shows none of the reconnecting, resumed, rejected or superseded notices,
and no **Start a new order** button. `onConnectionLost` returns first for those modes, exactly as before. Their
existing reconnect behaviour is unchanged:

- 4000 idle parks the socket;
- other closes reconnect.

## How it maps onto McDonald's

The reference design keeps a per-socket session in `session_manager.py` over an `order_state` singleton. McDonald's has the
same shape (`SessionManager` over `order_state_singleton`, keyed by session id), so the grace hold ports directly.
What differs:

- **Router in front.** `/realtime` is `ProcessorRouter`. Only cloud sockets reach `RTMiddleTier._websocket_handler`, so
  resume lives entirely in `RTMiddleTier` and `SessionManager`. The router and local mode are untouched.
- **Tools follow the resumed order.**
  - Every tool call looks up its order through `SessionManager.get_session_id(ws)`. A successful resume re-maps the
    new socket to the held session id, so tool calls on the resumed socket read and write the resumed order.
  - The tool-error seam still works there: a raising tool sends an apology `function_call_output` to the model, the
    socket stays open, and the next tool call works (`test_order_resume.py`).
- **R1 rate-limit recovery.** `RateLimitRecovery` is per socket.
  - A pending retry is cancelled when the browser drops (`recovery.close()`). A retry is never guest activity.
  - On resume, `recovery.set_session_id()` makes recovery logs carry the resumed session id.
  - The silent-guest nudge is skipped while a rate-limit retry is pending, because the retry is about to make the
    model speak. If the nudge itself is rate limited, R1's retry ladder retries it and no second nudge is sent.
- **Voice per upstream session.**
  - The middle tier applies the guest's voice to each new upstream session. On resume, `App` re-sends
    `extension.set_voice` before its `session.update`, so the resumed upstream uses the guest's voice.
  - The verbose-log flags are per socket and are re-sent too.
- **Context monitor.** McDonald's `rtmt` doesn't feed `ContextMonitor` with injected system text, including the
  greeting. The rehydration item and the nudge follow that rule.
- **Idle close 4000** already existed. Resume builds on it: an idle close ends the session before the socket closes,
  so a 4000 is never resumable.

## Lifecycle

| Event | What the backend does |
| --- | --- |
| Transport close (1001/1002/1006/1011, heartbeat timeout, handler exit, upstream connect failure) | The session is **detached**. Its order, transcript and resume credential are held for `min(resume.grace_seconds, remaining idle budget)`. |
| Idle close (4000 `idle_timeout`) | The session is **ended** before the socket closes. It can't be resumed. |
| `extension.end_session` from the browser | The session is ended and the socket closes with 1000 `session_ended`. |
| Hold expires, or the LRU cap (`resume.max_detached`) evicts the entry | The session is ended. This is checked every `resume.sweep_interval_seconds`, and again at resume time. |

The idle clock (`security.idle_timeout_seconds`, 300 s) runs from the guest's last activity. It **keeps running while
the guest is disconnected**, so a drop never extends the 5 minutes.

- **Guest activity:**
  - client text frames other than `input_audio_buffer.append`;
  - upstream `input_audio_buffer.speech_started`;
  - `conversation.item.input_audio_transcription.completed`.
- **Not guest activity:**
  - a continuously streaming mic;
  - the crew member's own speech, including the resume nudge;
  - a rate-limit retry.

`security.max_concurrent_sessions` counts attached sessions only. Held sessions don't take a slot.

Resume works within one backend process only. That's why the Dockerfile runs one gunicorn worker and the ingress uses
sticky affinity. See
[customizing_deploy.md](customizing_deploy.md#scaling-session-affinity-and-the-session-token-secret).

## Resume credential

- It's a 256-bit `secrets.token_urlsafe(32)` string, 43 characters long.
- It's delivered **only over the websocket**. It must never appear in a URL, a cookie, or a log.
  - The server stores and logs only its SHA-256. Logs show `sha256(id)[:8]`.
- It's single-use.
  - Every successful resume consumes it and returns a new one.
  - Re-announcing a session also rotates it.
  - The comparison is constant-time.
- It isn't bound to the signed-in user, and it isn't embedded in the HMAC session token. The HMAC token remains a pure
  connection gate.

## Wire protocol

All messages are JSON text frames on `/realtime?mode=cloud`.

### 1. Server → browser: `extension.session_metadata` (fresh session)

```json
{"type": "extension.session_metadata",
 "sessionToken": "…", "roundTripIndex": 0, "roundTripToken": "…",
 "resumeId": "<43-char id>"}
```

The server sends this once it has decided the connection is fresh **and** the upstream `session.created` has arrived.
A connection counts as fresh when:

- the browser's first frame is anything other than `extension.resume`; or
- no frame arrives within `resume.first_frame_timeout_seconds` (2 s).

So a browser that never resumes sees metadata up to 2 s after connecting, or as soon as it sends its first
`session.update`.

`resumeId` is omitted when `resume.enabled` is false. The fields are camelCase, as before.

**Browser:** store `resumeId` in `sessionStorage` under `mcdonalds.resumeId`. Resume is per tab by design.

### 2. Browser → server: `extension.resume` (must be the FIRST frame)

```json
{"type": "extension.resume", "resume_id": "<stored id>"}
```

Send it as the very first frame after `open`, before `session.update`, audio, or anything queued. The server honours
it only as the first frame, and only before the 2 s first-frame deadline. It's never forwarded to the model.

**Browser (as built):**

- When resume is active, `useRealtime` doesn't use react-use-websocket's own queue; every send is `keep=false`.
- While the socket is closed, the hook holds only `session.update` and extension frames in its own queue. Audio and
  cancels are dropped.
- `onOpen` runs synchronously, before the library could flush anything. It sends `extension.resume` first (if an id is
  stored), then the hook's queue.
- So a guest who taps while the socket is reconnecting still produces `extension.resume` → `session.update`, in that
  order.

No resume frame is sent in local mode, direct-AOAI mode, or Azure Speech mode. In those modes the hook keeps its
previous send semantics.

### 3a. Server → browser: `extension.session_resumed` (accepted)

```json
{"type": "extension.session_resumed",
 "order_summary": {"items": [...], "total": 5.69, "tax": 0.46, "finalTotal": 6.15},
 "session_token": "…", "round_trip_index": 3, "round_trip_token": "…",
 "resume_id": "<rotated id>"}
```

The fields are snake_case. `order_summary` is the parsed object, in the same shape the ticket already renders from a
tool response. It includes combo `components`.

This replaces `extension.session_metadata`; no metadata frame follows.

**Browser:**

1. Replace the stored id with `resume_id`.
2. Render `order_summary` onto the ticket.
3. Set the session and round-trip identifiers.
4. Restart the mic straight away. The backend has already sent its bootstrap `session.update`, so the session is ready
   for audio.

**Browser (as built):**

- If the guest was mid-conversation at the drop, the app re-sends:
  - `extension.set_voice`;
  - its own `session.update`, which restores the browser's VAD (threshold 0.7, 500 ms silence);
  - the verbose-log flags.

  It then restarts the mic. None of this triggers a greeting.
- If the guest tapped the mic during the reconnect, the same happens.
- `Recorder.start()` resolves `false` if the AudioContext is still suspended after 1.5 s. The app treats that, or a
  `getUserMedia` rejection, as "needs a gesture": it releases the mic and shows "Tap the mic to continue". That tap
  starts the mic immediately, because no greeting will come.
- If the guest wasn't talking at the drop, or after a reload, the ticket is restored and the mic stays off until a tap.

**Gesture behaviour (Chromium):**

- The AudioContext created on the guest's first tap is kept across mic stop and start, so it stays `running` through a
  drop.
- Mic permission persists for the origin.
- So the auto-restart needs no new gesture.
- A reload creates a new document, whose AudioContext starts `suspended` until a gesture. That's why a reload asks for
  a tap.

The crew member stays **silent** until the guest speaks; there's no "welcome back". If the guest hasn't spoken within
`resume.nudge_after_seconds` (30 s), the crew member asks once, briefly, whether they need anything else. The question
arrives as a normal response.

### 3b. Server → browser: `extension.resume_rejected`

```json
{"type": "extension.resume_rejected", "reason": "unknown"}
```

| `reason` | Meaning |
| --- | --- |
| `unknown` | The id is wrong or already used, the session was ended or evicted, or the order is gone. |
| `expired` | The grace hold or the idle budget ran out. |
| `malformed` | The id is missing, or isn't a 32–128 character string. |
| `disabled` | `resume.enabled` is false. |
| `not_first_frame` | `extension.resume` arrived after another frame or after the first-frame deadline. The socket's current session continues. |

A rejection is **always** followed by an `extension.session_metadata` for the socket's fresh (or current) session,
carrying a new `resumeId`.

**Browser:**

1. Drop the old id.
2. Clear the ticket and show a fresh order.
3. Store the new `resumeId`.

The app shows "We couldn't restore your order…". The exception is a guest who already tapped during the reconnect:
that tap carries straight on into the fresh session and its greeting.

### 4. Browser → server: `extension.end_session`

```json
{"type": "extension.end_session"}
```

The server ends the session, deleting the order immediately, and closes the socket with 1000 `session_ended`.

**Browser (as built):**

- The **Start a new order** button sends this. It clears the ticket and the id, and stops the mic.
- The 1000 `session_ended` close is final: there's no background reconnect and no resume.
- The hook then opens a **fresh** socket with a new token, as a page load would.
- Frames sent between `endSession()` and that close, such as a quick tap, are held for the fresh session.

### Upstream ordering on a resume

The new model connection receives, in order:

1. The bootstrap `session.update` (instructions, voice, tools).
2. **One** system `conversation.item.create`. It holds the current order JSON plus the last `resume.history_turns`
   guest/crew turns, capped at `resume.history_chars`, newest kept.
3. Only after that, guest audio and the browser's `session.update`.

No greeting and no `response.create` are sent until the guest speaks, or until the nudge fires. The nudge waits for
`session.updated`, like the greeting does.

If the resumed session was never greeted (the drop came before the conversation started), no rehydration item is sent
and the normal greeting runs.

## Close codes

| Code | Reason | Resumable? | Browser action |
| --- | --- | --- | --- |
| 1001/1002/1006/1011, etc. | transport | **Yes**, within the hold | Reconnect, send `extension.resume` first, and auto-restart the mic on `session_resumed`. |
| 4000 | `idle_timeout` | No; the session has already ended | Don't reconnect. Clear the stored id and show "session ended". |
| 4001, or a reason containing `expired` | token refresh (existing path) | Yes | Refresh the token, reconnect, then resume as above. |
| 4002 | `superseded` | n/a | Another socket, usually this tab's reconnect, took over the session. Don't reconnect, and keep the id: the new socket owns it. |
| 1000 | `session_ended` | No | This answers `extension.end_session`. Clear the id and don't reconnect. The app opens a fresh socket instead of resuming. |

When resume is active, `shouldReconnect` returns false for 4000, 4002 and 1000 `session_ended`. Without resume (local
mode and the other non-resume modes), only 4000 stops reconnection, as before.

A 4002 normally lands on a socket this tab has already abandoned. A live 4002 means a duplicated tab resumed the order
(duplicating a tab copies `sessionStorage`). This tab keeps its id, so its next tap presents that id, gets
`resume_rejected`, and starts fresh.

## Tests

- **Backend:** `app/backend/tests/test_order_resume.py` covers:
  - the grace hold;
  - the handshake;
  - rehydration and the nudge;
  - R1 interplay;
  - the tool-error seam on a resumed session.

  `app/backend/tests/test_infra_resume.py` covers the single worker, sticky ingress and the session secret.
- **Frontend (vitest, `npm test`):**
  - protocol: `src/hooks/__tests__/useRealtime.test.tsx`;
  - UI on a mounted `App`, including the local-mode and Azure Speech exclusions: `src/__tests__/App.resume.test.tsx`;
  - gesture timeout: `src/components/audio/__tests__/recorder.test.ts`;
  - notices: `src/components/ui/__tests__/status-message.test.tsx`.
- **Real browser:** `scripts/e2e_order_resume.py` runs the built frontend in headless Edge/Chromium against the real
  middle tier and a fake GA upstream. It uses no Azure and stays on localhost. It isn't part of the default test run:

  ```powershell
  .\.venv\Scripts\python.exe -m pip install playwright   # dev-only; install via your package proxy
  cd app/frontend; npm run build; cd ../..
  .\.venv\Scripts\python.exe scripts/e2e_order_resume.py   # --channel msedge (default) | chrome | chromium, --headed
  ```

  It covers:
  - a 1011 drop with auto-reconnect: same ticket and session; bootstrap → rehydration → no greeting; the mic back
    without a tap; one nudge;
  - the gesture fallback;
  - a tap during the reconnect;
  - a same-tab reload;
  - idle 4000: no reconnect, and a fresh session on tap;
  - **Start a new order**;
  - a strict-autoplay browser;
  - resume ids never appearing in URLs or server logs.

## Configuration (`app/backend/config.yaml`)

| Key | Default | Meaning |
| --- | --- | --- |
| `security.idle_timeout_seconds` | 300 | Idle budget from the guest's last activity. It keeps running while the guest is disconnected. |
| `resume.enabled` | true | When false, transport closes end the session and no `resumeId` is issued. |
| `resume.grace_seconds` | 120 | Maximum hold after a transport drop. The actual hold is `min(grace, remaining idle budget)`. |
| `resume.max_detached` | 20 | LRU cap on held sessions per worker. |
| `resume.history_turns` | 6 | Number of transcript turns replayed into the new upstream. 0 disables the transcript. |
| `resume.history_chars` | 2000 | Character cap on the replayed transcript. |
| `resume.nudge_after_seconds` | 30 | Silent-guest nudge after a resume. 0 disables it. |
| `resume.first_frame_timeout_seconds` | 2.0 | How long to wait for `extension.resume` before treating the connection as fresh. |
| `resume.sweep_interval_seconds` | 15 | How often idle and grace expiry are checked. |
| env `APP_SESSION_SECRET` | random per process | HMAC key for `/api/auth/session` tokens, shared across replicas. Set by bicep. |
