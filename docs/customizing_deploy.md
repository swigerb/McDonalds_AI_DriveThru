# Customizing the VoiceRAG deployment

This guide shows you how to customize the [VoiceRAG](../README.md#deploying-the-app) deployment to specify different options.
If your goal is to reuse existing services (OpenAI or Search), see the [existing services guide](./existing_services.md) instead.

## Customizing the real-time voice choice

The default crew voice is `marin` (set in `app/backend/config.yaml` `model.default_voice` and in
`infra/main.parameters.json`). Guests can also switch voices from the settings dialog.
To change the deployed default, run:

```bash
azd env set AZURE_OPENAI_REALTIME_VOICE_CHOICE <marin, cedar, alloy, ash, ballad, coral, echo, sage, shimmer, or verse>
```

These are the ten built-in voices `gpt-realtime-2.1` accepts (other names such as `fable`, `onyx` or `nova`
are rejected). OpenAI recommends `marin` and `cedar` for the best quality.

> An `azd env` value overrides the default in `infra/main.parameters.json`, so an environment created before
> the default changed keeps its old voice until you `azd env set` it.

Once you have set the voice choice, run `azd up` to apply the changes to the deployed app.
If you've already run `azd up` and want to first preview the voice with the development server, then update your local `.env` file by running `./scripts/write_env.sh` or `pwsh ./scripts/write_env.ps1`, and then restart the development server.

## Realtime model, reasoning effort and transcription

The template deploys `gpt-realtime-2.1` (version `2026-07-07`, GlobalStandard). `gpt-realtime-1.5` remains a
supported rollback: `azd env set AZURE_OPENAI_REALTIME_DEPLOYMENT gpt-realtime-1.5`.

The deployment name is configurable (`AZURE_OPENAI_REALTIME_DEPLOYMENT`). On the shared demo account McDonald's
uses `gpt-realtime-2.1-dz` (the same model on a DataZoneStandard deployment), so a custom or suffixed name is fine.

`model.reasoning_effort` in `app/backend/config.yaml` (default `low`; env `AZURE_OPENAI_REALTIME_REASONING_EFFORT`,
`off` disables) is sent as `session.reasoning.effort` only when the deployment is a reasoning model.
`model.reasoning_model` (env `AZURE_OPENAI_REALTIME_REASONING_MODEL`: `auto` | `true` | `false`) says whether it is;
`auto` infers it from the deployment name (`gpt-realtime-1.5`, `gpt-realtime`, `gpt-realtime-mini`, `gpt-4o-*` are
treated as non-reasoning). If the service still rejects a session update, the backend resends a minimal one
(instructions + tools only), so the tools always register.

- `gpt-realtime-2.1` accepts efforts `none`, `minimal`, `low`, `medium`, `high` and `xhigh`; `gpt-realtime-1.5`
  rejects `reasoning` at every level, and rejects `parallel_tool_calls: true`.
- `low` is the default: in the Sonic reference benchmark (174 live trials) it was 30/30 correct with the best
  first-audio p90 (1.57 s) and never called a tool before speaking. `none`/`minimal` often call a tool first,
  which leaves the guest in silence.
- `parallel_tool_calls` defaults to `null` (service default; 2.1 already batches calls, `false` is ~1.3 s slower).
- Transcription: `whisper-1` works with no extra deployment. `gpt-4o-transcribe` / `gpt-4o-mini-transcribe` pass
  `session.update`, but every turn then fails with `DeploymentNotFound` unless you deploy that model on the resource.

## Post-deploy realtime smoke check

GA rejects a `session.update` **wholesale** if any one field is unsupported, and the tools go with it: the crew
member keeps talking but never records the order. Every `session.update` the middle tier sends carries an
`event_id`; if the service rejects one, the middle tier logs it and resends a minimal update (instructions + tools
only), once. A tool that fails at runtime (e.g. an Azure AI Search error) returns an apology to the model instead
of ending the conversation.

After `azd deploy` / `azd up`, a **non-fatal** `postdeploy` hook runs `scripts/smoke_realtime.py`. It builds the
exact bootstrap, relayed-browser and fallback `session.update` payloads from the app code (`config.yaml`, the real
system prompt and `tools.attach_tools_rtmt`), sends them to the deployed realtime model, and checks each comes back
as `session.updated` with all four tools, `tool_choice: auto`, the instructions and (on 2.1) the reasoning effort.
It then checks that guest speech is actually transcribed with the configured transcription model: the model
reads a fixed order aloud (the phrase goes in the response instructions, not a user turn, so it recites rather
than answers) and the transcript must match that phrase **word for word** (case, punctuation and one or two
whisper slips aside; `TRANSCRIPTION_MIN_SIMILARITY` = 0.85). A transcript like "Sure, I can't place the order
for you..." fails the check instead of passing on a keyword.

- It never fails the deployment; problems are printed as a loud warning. Exit codes of the Python script:
  `0` pass, `1` a check failed, `2` could not run (auth, network, missing settings).
- Run it by hand: `python scripts/smoke_realtime.py` (reads the azd env), or
  `python scripts/smoke_realtime.py --endpoint https://<aoai>.openai.azure.com/ --deployment gpt-realtime-1.5`.
  Auth: `AZURE_OPENAI_EASTUS2_API_KEY` if set, else an Entra ID token for the azd env's `AZURE_TENANT_ID` /
  `AZURE_SUBSCRIPTION_ID` ("Cognitive Services OpenAI User"), not whichever `az` / `azd` account is active; a
  token from another tenant gets HTTP 400 "Tenant provided in token does not match resource token". Override
  with `--tenant <id>` / `--subscription <id>` (precedence: flag > environment variable > azd env). It tries
  `az` for that subscription, then `azd` and `az` pinned to that tenant, and reports every failure if all fail.
- Skip it: `azd env set MCD_SKIP_REALTIME_SMOKE true`.
- Local mode (Phi-4/Piper) is not checked; it never talks to Azure OpenAI.

## Rate-limit recovery

When the realtime deployment is out of quota for a moment (an `error` whose code or type contains `rate_limit`,
or a `response.done` with `status: failed` for that reason) the middle tier retries the reply instead of leaving
the guest in silence (`app/backend/rate_limit.py`):

1. First hit: a silent `response.create` retry after `retry_delay_seconds` (1.5 s), or the service's "try again in
   N s" hint clamped to 0.5-5 s.
2. Second hit: the browser gets `extension.rate_limited` and plays a short local apology clip ("Sorry, give me just
   a second.") with the mic muted, and the middle tier retries once more after `second_retry_delay_seconds` (4 s,
   hint clamped to 2-8 s).
3. After `max_retries` (2): `extension.rate_limited` with `final: true`; the guest sees "please say that again" and
   the mic reopens. Nothing more is retried.

Guest speech (barge-in) or any new response cancels a pending retry and resets the ladder, so a retry never talks
over the guest. Rate limits on a `session.update` keep the minimal-update fallback above. Settings live under
`resilience.rate_limit` in `app/backend/config.yaml`; `RATE_LIMIT_RECOVERY_ENABLED=false` turns it off.

The clips are `app/frontend/public/audio/rate-limit-apology-<lang>.wav`, one per UI locale (en, es, fr, ja; any
other UI language plays English). They are pre-recorded because the model is the thing that is rate-limited.
Regenerate them after a voice change with `python scripts/generate_apology_clips.py [--deployment <name>] [--voice
marin]`; it reads each clip back through whisper-1 so you can check the wording. Adding a UI locale needs a phrase
in that script and in `src/lib/rate-limit-apology.ts` (`tests/test_apology_clips.py` fails until it has a clip).
