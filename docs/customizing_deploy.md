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
