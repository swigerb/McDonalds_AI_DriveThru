import useWebSocket, { ReadyState } from "react-use-websocket";
import { useRef, useCallback, useEffect, useState } from "react";

import {
    InputAudioBufferAppendCommand,
    InputAudioBufferClearCommand,
    Message,
    ResponseAudioDelta,
    ResponseAudioTranscriptDelta,
    ResponseDone,
    SessionUpdateCommand,
    ExtensionMiddleTierToolResponse,
    ResponseInputAudioTranscriptionCompleted,
    ExtensionSessionMetadata,
    ExtensionRoundTripToken,
    ExtensionRateLimited
} from "@/types";

export { ReadyState };

type Parameters = {
    localMode?: boolean; // When true, WebSocket connects to localhost:8000 for offline operation
    useDirectAoaiApi?: boolean; // If true, the middle tier will be skipped and the AOAI ws API will be called directly
    aoaiEndpointOverride?: string;
    aoaiApiKeyOverride?: string;
    aoaiModelOverride?: string;

    enableInputAudioTranscription?: boolean;
    onWebSocketOpen?: () => void;
    onWebSocketClose?: () => void;
    /** Fired whenever an open socket closes. The server session (and its order) is gone. */
    onConnectionLost?: (info: ConnectionLostInfo) => void;
    onWebSocketError?: (event: Event) => void;
    onWebSocketMessage?: (event: MessageEvent<any>) => void;

    onReceivedResponseCreated?: (message: Message) => void;
    onReceivedResponseAudioDelta?: (message: ResponseAudioDelta) => void;
    onReceivedInputAudioBufferSpeechStarted?: (message: Message) => void;
    onReceivedResponseDone?: (message: ResponseDone) => void;
    onReceivedExtensionMiddleTierToolResponse?: (message: ExtensionMiddleTierToolResponse) => void;
    onReceivedSessionMetadata?: (message: ExtensionSessionMetadata) => void;
    onReceivedRoundTripToken?: (message: ExtensionRoundTripToken) => void;
    onReceivedExtensionRateLimited?: (message: ExtensionRateLimited) => void;
    onReceivedResponseAudioTranscriptDelta?: (message: ResponseAudioTranscriptDelta) => void;
    onReceivedInputAudioTranscriptionCompleted?: (message: ResponseInputAudioTranscriptionCompleted) => void;
    onReceivedError?: (message: Message) => void;
};

// Server closes idle sessions with this code (session_manager.IDLE_CLOSE_CODE).
// It is intentional, so the hook stays disconnected until the guest taps again
// instead of silently opening a new socket that mic audio could leak into.
export const WS_CLOSE_IDLE_TIMEOUT = 4000;

export type ConnectionLostInfo = { code: number; reason: string; idle: boolean };

// Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
const MAX_RETRIES = 10;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

async function fetchSessionToken(): Promise<string | null> {
    try {
        const resp = await fetch("/api/auth/session");
        if (!resp.ok) return null;
        const data = await resp.json();
        return data.token ?? null;
    } catch {
        // Endpoint doesn't exist or server unavailable — graceful fallback
        return null;
    }
}

export default function useRealTime({
    localMode,
    useDirectAoaiApi,
    aoaiEndpointOverride,
    aoaiApiKeyOverride,
    aoaiModelOverride,
    enableInputAudioTranscription,
    onWebSocketOpen,
    onWebSocketClose,
    onConnectionLost,
    onWebSocketError,
    onWebSocketMessage,
    onReceivedResponseCreated,
    onReceivedResponseDone,
    onReceivedResponseAudioDelta,
    onReceivedResponseAudioTranscriptDelta,
    onReceivedInputAudioBufferSpeechStarted,
    onReceivedExtensionMiddleTierToolResponse,
    onReceivedInputAudioTranscriptionCompleted,
    onReceivedSessionMetadata,
    onReceivedRoundTripToken,
    onReceivedExtensionRateLimited,
    onReceivedError
}: Parameters) {
    const [sessionToken, setSessionToken] = useState<string | null>(null);
    // Don't open the socket until the token fetch settles, otherwise the first
    // (token-less) socket is torn down and replaced as soon as the token arrives.
    const [tokenReady, setTokenReady] = useState(!!localMode || !!useDirectAoaiApi);
    // false after an idle close or exhausted retries (cloud only): stay down until the guest taps.
    const [shouldConnect, setShouldConnect] = useState(true);

    // Fetch a session token on mount (graceful — null means no token required)
    // In local mode, skip — no Azure auth needed for offline operation
    useEffect(() => {
        if (localMode) {
            console.log("[LOCAL-MODE] Skipping session token fetch — running locally");
            setSessionToken(null);
            setTokenReady(true);
            return;
        }
        if (useDirectAoaiApi) return;
        console.log("[WS] Fetching session token...");
        fetchSessionToken().then((token) => {
            console.log("[WS] Session token:", token ? "obtained" : "not required");
            setSessionToken(token);
            setTokenReady(true);
        });
    }, [localMode, useDirectAoaiApi]);

    const buildWsEndpoint = () => {
        if (localMode) {
            // Use the same host the page was loaded from — avoids cross-origin issues.
            // The browser already resolved this hostname to load the page, so it works offline too.
            const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            const url = `${wsProtocol}//${window.location.host}/realtime?mode=local`;
            console.log("[LOCAL-MODE] WebSocket endpoint:", url);
            return url;
        }
        if (useDirectAoaiApi) {
            return `${aoaiEndpointOverride}/openai/v1/realtime?api-key=${aoaiApiKeyOverride}&model=${aoaiModelOverride}`;
        }
        const base = `/realtime`;
        if (sessionToken) {
            return `${base}?token=${encodeURIComponent(sessionToken)}&mode=cloud`;
        }
        return `${base}?mode=cloud`;
    };

    const wsEndpoint = buildWsEndpoint();

    const retryCountRef = useRef(0);
    const [retryCount, setRetryCount] = useState(0);
    const prevReadyStateRef = useRef<ReadyState | null>(null);
    // Ref to break circular dependency: callbacks need sendJsonMessage,
    // but sendJsonMessage comes from useWebSocket which takes the callbacks.
    const sendJsonMessageRef = useRef<(msg: object, keep?: boolean) => void>(() => {});

    const onMessageReceived = useCallback((event: MessageEvent<any>) => {
        onWebSocketMessage?.(event);

        let message: Message;
        try {
            message = JSON.parse(event.data);
        } catch (e) {
            console.error("Failed to parse JSON message:", e);
            throw e;
        }

        switch (message.type) {
            case "response.created":
                // Earliest signal that the AI is about to speak.
                // Flush any buffered mic audio on the server to prevent echo.
                sendJsonMessageRef.current({ type: "input_audio_buffer.clear" }, false);
                onReceivedResponseCreated?.(message);
                break;
            case "response.done":
                onReceivedResponseDone?.(message as ResponseDone);
                break;
            case "response.audio.delta":
                onReceivedResponseAudioDelta?.(message as ResponseAudioDelta);
                break;
            case "response.audio_transcript.delta":
                onReceivedResponseAudioTranscriptDelta?.(message as ResponseAudioTranscriptDelta);
                break;
            case "input_audio_buffer.speech_started":
                onReceivedInputAudioBufferSpeechStarted?.(message);
                break;
            case "conversation.item.input_audio_transcription.completed":
                onReceivedInputAudioTranscriptionCompleted?.(message as ResponseInputAudioTranscriptionCompleted);
                break;
            case "extension.middle_tier_tool_response":
                onReceivedExtensionMiddleTierToolResponse?.(message as ExtensionMiddleTierToolResponse);
                break;
            case "extension.session_metadata":
                onReceivedSessionMetadata?.(message as ExtensionSessionMetadata);
                break;
            case "extension.round_trip_token":
                onReceivedRoundTripToken?.(message as ExtensionRoundTripToken);
                break;
            case "extension.rate_limited":
                onReceivedExtensionRateLimited?.(message as ExtensionRateLimited);
                break;
            case "error":
                onReceivedError?.(message);
                break;
        }
    }, [
        onWebSocketMessage,
        onReceivedResponseCreated,
        onReceivedResponseDone,
        onReceivedResponseAudioDelta,
        onReceivedResponseAudioTranscriptDelta,
        onReceivedInputAudioBufferSpeechStarted,
        onReceivedInputAudioTranscriptionCompleted,
        onReceivedExtensionMiddleTierToolResponse,
        onReceivedSessionMetadata,
        onReceivedRoundTripToken,
        onReceivedExtensionRateLimited,
        onReceivedError
    ]);

    const { sendJsonMessage, readyState } = useWebSocket(tokenReady ? wsEndpoint : null, {
        onOpen: () => {
            console.log("[WS] Connection opened", localMode ? "(local mode)" : "(cloud mode)", "→", wsEndpoint);
            retryCountRef.current = 0;
            setRetryCount(0);
            onWebSocketOpen?.();
        },
        onClose: (event) => {
            console.log("[WS] Connection closed", { code: event.code, reason: event.reason, localMode });
            setRetryCount(prev => prev + 1);
            const idle = event.code === WS_CLOSE_IDLE_TIMEOUT;
            if (idle) {
                setShouldConnect(false);
            } else if (!localMode && (event.code === 4001 || event.reason?.includes("expired"))) {
                // Auth failure → refresh token and let reconnect use the new one
                // Skip in local mode — no Azure auth needed
                fetchSessionToken().then(setSessionToken);
            }
            onConnectionLost?.({ code: event.code, reason: event.reason ?? "", idle });
            onWebSocketClose?.();
        },
        onError: event => {
            console.error("[WS] Connection error:", event, localMode ? "(local mode)" : "(cloud mode)");
            onWebSocketError?.(event);
        },
        onMessage: onMessageReceived,
        shouldReconnect: (event: CloseEvent) => event.code !== WS_CLOSE_IDLE_TIMEOUT,
        // Local mode keeps its old behaviour (the mic tap shows a toast instead).
        onReconnectStop: () => {
            if (!localMode) setShouldConnect(false);
        },
        reconnectAttempts: MAX_RETRIES,
        reconnectInterval: (attemptNumber: number) => {
            const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attemptNumber), MAX_DELAY_MS);
            // Add jitter to prevent thundering herd
            return delay + Math.random() * 500;
        }
    }, shouldConnect);

    const isConnected = readyState === ReadyState.OPEN;
    const needsReconnect = !shouldConnect;

    // Re-open after an idle close or exhausted retries, with a fresh token
    // (the old one may have expired while the page sat idle).
    const reconnect = useCallback(async () => {
        if (shouldConnect) return;
        if (!localMode && !useDirectAoaiApi) setSessionToken(await fetchSessionToken());
        setRetryCount(0);
        setShouldConnect(true);
    }, [shouldConnect, localMode, useDirectAoaiApi]);

    // Keep ref in sync so onMessageReceived can call sendJsonMessage
    useEffect(() => {
        sendJsonMessageRef.current = sendJsonMessage;
    }, [sendJsonMessage]);

    // Log readyState transitions for diagnostics
    useEffect(() => {
        const labels: Record<number, string> = {
            [ReadyState.CONNECTING]: "CONNECTING",
            [ReadyState.OPEN]: "OPEN",
            [ReadyState.CLOSING]: "CLOSING",
            [ReadyState.CLOSED]: "CLOSED",
            [-1]: "UNINSTANTIATED"
        };
        const prev = prevReadyStateRef.current;
        const prevLabel = prev !== null ? (labels[prev] ?? `UNKNOWN(${prev})`) : "NONE";
        const currLabel = labels[readyState] ?? `UNKNOWN(${readyState})`;
        console.log(`[WS-DIAG] readyState changed: ${prevLabel} → ${currLabel}`);
        console.log(`[WS-DIAG] WebSocket URL: ${wsEndpoint}`);
        prevReadyStateRef.current = readyState;
    }, [readyState, wsEndpoint]);

    const startSession = () => {
        console.log("[WS] Starting session. readyState:", readyState, readyState === ReadyState.OPEN ? "OPEN" : "NOT OPEN");
        const command: SessionUpdateCommand = {
            type: "session.update",
            session: {
                turn_detection: {
                    type: "server_vad",
                    threshold: 0.7,
                    prefix_padding_ms: 300,
                    silence_duration_ms: 500
                }
            }
        };

        if (enableInputAudioTranscription) {
            command.session.input_audio_transcription = {
                model: "whisper-1"
            };
        }

        sendJsonMessage(command);
    };

    const addUserAudio = (base64Audio: string) => {
        const command: InputAudioBufferAppendCommand = {
            type: "input_audio_buffer.append",
            audio: base64Audio
        };

        // keep=false: drop, never queue, audio while the socket isn't open —
        // queued frames are replayed onto the next socket ahead of session.update.
        sendJsonMessage(command, false);
    };

    const inputAudioBufferClear = () => {
        const command: InputAudioBufferClearCommand = {
            type: "input_audio_buffer.clear"
        };

        sendJsonMessage(command, false);
    };

    const cancelResponse = () => {
        sendJsonMessage({ type: "response.cancel" }, false);
    };

    const sendVerboseLogging = (enabled: boolean) => {
        sendJsonMessage({ type: "extension.set_verbose_logging", enabled });
    };

    const sendLogToFile = (enabled: boolean) => {
        sendJsonMessage({ type: "extension.set_log_to_file", enabled });
    };

    const sendVoiceChoice = (voice: string) => {
        console.log(`[VOICE] Sending voice choice to backend: ${voice}`);
        sendJsonMessage({ type: "extension.set_voice", voice });
    };

    const sendLocalModeToggle = (enabled: boolean) => {
        console.log("[LOCAL-MODE] Sending local mode toggle:", enabled, "readyState:", readyState);
        sendJsonMessage({ type: "extension.set_local_mode", enabled });
    };

    const sendPiperVoiceChoice = (voice: string) => {
        console.log(`[VOICE] Sending Piper voice choice to backend: ${voice}`);
        sendJsonMessage({ type: "extension.set_piper_voice", voice });
    };

    return {
        startSession,
        addUserAudio,
        inputAudioBufferClear,
        cancelResponse,
        sendVerboseLogging,
        sendLogToFile,
        sendVoiceChoice,
        sendLocalModeToggle,
        sendPiperVoiceChoice,
        readyState,
        wsEndpoint,
        retryCount,
        maxRetries: MAX_RETRIES,
        isConnected,
        needsReconnect,
        reconnect
    };
}
