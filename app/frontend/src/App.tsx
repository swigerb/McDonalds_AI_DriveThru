import { useState, useEffect, useRef, useMemo, useCallback, lazy, Suspense, memo } from "react";
import { Mic, MicOff, Menu, MessageSquare, LogOut, Github, ChevronDown } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

import StatusMessage from "@/components/ui/status-message";
import MenuPanel from "@/components/ui/menu-panel";
import OrderSummary, { calculateOrderSummary, OrderSummaryProps } from "@/components/ui/order-summary";
import TranscriptPanel from "@/components/ui/transcript-panel";
const Settings = lazy(() => import("@/components/ui/settings"));
import useRealTime from "@/hooks/useRealtime";
import useAzureSpeech from "@/hooks/useAzureSpeech";
import useAudioRecorder from "@/hooks/useAudioRecorder";
import useAudioPlayer from "@/hooks/useAudioPlayer";

import { ExtensionMiddleTierToolResponse, ExtensionRoundTripToken, ExtensionSessionMetadata } from "./types";

import { ThemeProvider, useTheme } from "./context/theme-context";
import { DummyDataProvider, useDummyDataContext } from "@/context/dummy-data-context";
import { AzureSpeechProvider, useAzureSpeechOnContext } from "@/context/azure-speech-context";
import { AuthProvider, useAuth } from "@/context/auth-context";

import dummyTranscriptsData from "@/data/dummyTranscripts.json";
import dummyOrderData from "@/data/dummyOrder.json";
import azureLogo from "@/assets/azurelogo.svg";
import mcdonaldsLogo from "@/assets/mcdonalds-logo.svg";

type HighlightTone = "red" | "blue" | "yellow";

type SessionIdentifiersState = {
    sessionToken: string;
    roundTripIndex: number;
    roundTripToken: string;
};

const heroHighlights: Array<{ title: string; detail: string; tone: HighlightTone }> = [
    {
        title: "Rewards Ready",
        detail: "Voice orders auto-sync with McDonald's Rewards and deals",
        tone: "red"
    },
    {
        title: "Azure Infusion",
        detail: "Azure OpenAI + Speech keep conversations flowing",
        tone: "blue"
    },
    {
        title: "Live Menu",
        detail: "Azure AI Search keeps McDonald's menu items current",
        tone: "yellow"
    }
];

const heroCallouts = [
    { label: "Featured Item", value: "Big Mac", accent: "#DB0007" },
    { label: "Crew Pick", value: "Quarter Pounder® w/ Cheese", accent: "#27251F" }
];

function McDonaldsApp() {
    const [isRecording, setIsRecording] = useState(false);
    const [isMobile, setIsMobile] = useState(false);
    const { useAzureSpeechOn } = useAzureSpeechOnContext();
    const { useDummyData } = useDummyDataContext();
    const { theme } = useTheme();
    const { logout, authEnabled } = useAuth();

    const [transcripts, setTranscripts] = useState<Array<{ text: string; isUser: boolean; timestamp: Date }>>([]);
    const dummyTranscripts = useMemo<Array<{ text: string; isUser: boolean; timestamp: Date }>>(
        () =>
            dummyTranscriptsData.map(transcript => ({
                ...transcript,
                timestamp: new Date(transcript.timestamp)
            })),
        []
    );

    const initialOrder: OrderSummaryProps = {
        items: [],
        total: 0,
        tax: 0,
        finalTotal: 0
    };

    const dummyOrder = useMemo<OrderSummaryProps>(() => calculateOrderSummary(dummyOrderData), []);

    const [order, setOrder] = useState<OrderSummaryProps>(initialOrder);
    const [sessionIdentifiers, setSessionIdentifiers] = useState<SessionIdentifiersState | null>(null);
    const [tokenHistory, setTokenHistory] = useState<SessionIdentifiersState[]>([]);
    const [showSessionTokens, setShowSessionTokens] = useState<boolean>(() => {
        if (typeof window === "undefined") return true;
        const stored = localStorage.getItem("showSessionTokens");
        return stored === null ? true : stored === "true";
    });
    const [verboseLogging, setVerboseLogging] = useState<boolean>(() => {
        return localStorage.getItem("verboseLogging") === "true";
    });
    const [logToFile, setLogToFile] = useState<boolean>(() => {
        return localStorage.getItem("verboseLogToFile") === "true";
    });

    useEffect(() => {
        localStorage.setItem("showSessionTokens", showSessionTokens.toString());
    }, [showSessionTokens]);

    useEffect(() => {
        localStorage.setItem("verboseLogging", verboseLogging.toString());
    }, [verboseLogging]);

    useEffect(() => {
        localStorage.setItem("verboseLogToFile", logToFile.toString());
    }, [logToFile]);

    const handleSessionIdentifiers = useCallback((message: ExtensionSessionMetadata | ExtensionRoundTripToken) => {
        const snapshot: SessionIdentifiersState = {
            sessionToken: message.sessionToken,
            roundTripIndex: message.roundTripIndex,
            roundTripToken: message.roundTripToken
        };
        setSessionIdentifiers(snapshot);
        setTokenHistory(prev => [snapshot, ...prev]);
    }, []);

    const isSessionActiveRef = useRef(false);
    const awaitingGreetingDoneRef = useRef(false);
    const greetingAudioSeenRef = useRef(false);
    const startMicInFlightRef = useRef<Promise<void> | null>(null);
    const isAiSpeakingRef = useRef(false);

    const realtime = useRealTime({
        enableInputAudioTranscription: true,
        onWebSocketOpen: () => console.log("WebSocket connection opened"),
        onWebSocketClose: () => console.log("WebSocket connection closed"),
        onWebSocketError: event => console.error("WebSocket error:", event),
        onReceivedError: message => console.error("error", message),
        onReceivedResponseCreated: () => {
            if (!isSessionActiveRef.current) return;
            // Mute mic at the EARLIEST response signal — before audio deltas arrive.
            // The server also receives input_audio_buffer.clear (sent by useRealTime)
            // to flush any echo already in the pipeline.
            if (!isAiSpeakingRef.current) {
                isAiSpeakingRef.current = true;
                muteAudioRecording();
            }
        },
        onReceivedResponseAudioDelta: message => {
            if (!isSessionActiveRef.current) return;
            greetingAudioSeenRef.current = true;
            playAudio(message.delta);
        },
        onReceivedInputAudioBufferSpeechStarted: () => {
            // User speech detected - stop AI playback (barge-in) and unmute mic
            stopAudioPlayer();
            if (isAiSpeakingRef.current) {
                isAiSpeakingRef.current = false;
                unmuteAudioRecording();
            }
        },
        onReceivedExtensionMiddleTierToolResponse: ({ tool_name, tool_result }: ExtensionMiddleTierToolResponse) => {
            if (tool_name === "update_order" || tool_name === "get_order" || tool_name === "reset_order") {
                const orderSummary: OrderSummaryProps = JSON.parse(tool_result);
                setOrder(orderSummary);

                console.log("Order Total:", orderSummary.total);
                console.log("Tax:", orderSummary.tax);
                console.log("Final Total:", orderSummary.finalTotal);
            }
        },
        onReceivedSessionMetadata: handleSessionIdentifiers,
        onReceivedRoundTripToken: handleSessionIdentifiers,
        onReceivedInputAudioTranscriptionCompleted: message => {
            const newTranscriptItem = {
                text: message.transcript,
                isUser: true,
                timestamp: new Date()
            };
            setTranscripts(prev => [...prev, newTranscriptItem]);
        },
        onReceivedResponseDone: message => {
            const transcript = message.response.output.map(output => output.content?.map(content => content.transcript).join(" ")).join(" ");
            if (!transcript) return;

            const newTranscriptItem = {
                text: transcript,
                isUser: false,
                timestamp: new Date()
            };
            setTranscripts(prev => [...prev, newTranscriptItem]);

            // AI finished speaking - unmute the microphone
            if (isAiSpeakingRef.current) {
                isAiSpeakingRef.current = false;
                unmuteAudioRecording();
            }

            if (awaitingGreetingDoneRef.current && isSessionActiveRef.current) {
                awaitingGreetingDoneRef.current = false;

                if (!startMicInFlightRef.current) {
                    startMicInFlightRef.current = (async () => {
                        // If we received audio deltas for the greeting, wait until playback drains.
                        if (greetingAudioSeenRef.current) {
                            await waitForAudioDrain(2000);
                        }

                        if (!isSessionActiveRef.current) return;
                        await startAudioRecording();
                    })().finally(() => {
                        startMicInFlightRef.current = null;
                    });
                }
            }
        }
    });

    const azureSpeech = useAzureSpeech({
        onReceivedToolResponse: ({ tool_name, tool_result }: ExtensionMiddleTierToolResponse) => {
            if (tool_name === "update_order") {
                const orderSummary: OrderSummaryProps = JSON.parse(tool_result);
                setOrder(orderSummary);

                console.log("Order Total:", orderSummary.total);
                console.log("Tax:", orderSummary.tax);
                console.log("Final Total:", orderSummary.finalTotal);
            }
        },
        onSpeechToTextTranscriptionCompleted: (message: { transcript: string }) => {
            const newTranscriptItem = {
                text: message.transcript,
                isUser: true,
                timestamp: new Date()
            };
            setTranscripts(prev => [...prev, newTranscriptItem]);
        },
        onModelResponseDone: (message: { response: { output: Array<{ content?: Array<{ transcript: string }> }> } }) => {
            const transcript = message.response.output
                .map(output => output.content?.map(content => content.transcript).join(" "))
                .join(" ");
            if (!transcript) return;

            const newTranscriptItem = {
                text: transcript,
                isUser: false,
                timestamp: new Date()
            };
            setTranscripts(prev => [...prev, newTranscriptItem]);
        },
        onError: (error: unknown) => console.error("Error:", error)
    });

    const { reset: resetAudioPlayer, play: playAudio, stop: stopAudioPlayer, waitForDrain: waitForAudioDrain } =
        useAudioPlayer();

    // Barge-in handler: when the Recorder detects the user speaking while
    // the mic is muted (AI is talking), unmute, cancel the AI response,
    // and stop audio playback so the user can be heard immediately.
    // BLOCKED during greeting to prevent echo-driven greeting loop.
    const handleBargeIn = useCallback(() => {
        if (!isAiSpeakingRef.current) return;
        if (awaitingGreetingDoneRef.current) return;
        console.log("Barge-in detected — interrupting AI");
        isAiSpeakingRef.current = false;
        stopAudioPlayer();
        // Cancel the AI's in-flight response so the middleware also
        // resets echo suppression and lets our audio through.
        realtime.cancelResponse();
    }, [stopAudioPlayer, realtime]);

    const { start: startAudioRecording, stop: stopAudioRecording, mute: muteAudioRecording, unmute: unmuteAudioRecording } = useAudioRecorder({
        onAudioRecorded: useAzureSpeechOn ? azureSpeech.addUserAudio : realtime.addUserAudio,
        onBargeIn: handleBargeIn
    });

    const onToggleListening = async () => {
        if (!isRecording) {
            setSessionIdentifiers(null);

            // Start session and playback immediately, but delay mic capture until the greeting finishes.
            isSessionActiveRef.current = true;
            isAiSpeakingRef.current = false;
            awaitingGreetingDoneRef.current = !useAzureSpeechOn;
            greetingAudioSeenRef.current = false;

            await resetAudioPlayer();

            if (useAzureSpeechOn) {
                // AzureSpeech mode doesn't play a synthesized greeting audio stream.
                azureSpeech.startSession();
                await startAudioRecording();
            } else {
                realtime.startSession();
                if (verboseLogging) {
                    realtime.sendVerboseLogging(true);
                    if (logToFile) {
                        realtime.sendLogToFile(true);
                    }
                }

                // Safety: if we never receive the greeting completion, start the mic after a short timeout.
                window.setTimeout(() => {
                    if (!isSessionActiveRef.current) return;
                    if (!awaitingGreetingDoneRef.current) return;
                    awaitingGreetingDoneRef.current = false;
                    if (startMicInFlightRef.current) return;
                    startMicInFlightRef.current = startAudioRecording().finally(() => {
                        startMicInFlightRef.current = null;
                    });
                }, 3500);
            }

            setIsRecording(true);
        } else {
            await stopAudioRecording();
            stopAudioPlayer();
            isSessionActiveRef.current = false;
            isAiSpeakingRef.current = false;
            awaitingGreetingDoneRef.current = false;
            if (useAzureSpeechOn) {
                azureSpeech.inputAudioBufferClear();
            } else {
                realtime.inputAudioBufferClear();
            }
            setIsRecording(false);
        }
    };

    const { t } = useTranslation();

    useEffect(() => {
        const checkMobile = () => {
            setIsMobile(window.innerWidth < 768);
        };
        checkMobile();
        window.addEventListener("resize", checkMobile);
        return () => window.removeEventListener("resize", checkMobile);
    }, []);

    return (
        <div className={`min-h-screen bg-background p-4 text-foreground ${theme}`}>
            <div className="mx-auto max-w-7xl space-y-6">
                <div className="flex flex-col gap-3 text-sm font-semibold text-primary md:flex-row md:items-center md:justify-between">
                    <a
                        href="https://github.com/swigerb/SonicAIDriveThru"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 rounded-full bg-white/80 px-3 py-1 text-primary transition hover:text-accent"
                        title="View McDonald's Voice Ordering source"
                    >
                        <Github className="h-4 w-4" />
                        <span>Source on GitHub</span>
                    </a>
                    <div className="flex items-center gap-2">
                        <Suspense fallback={null}>
                            <Settings
                                isMobile={isMobile}
                                showSessionTokens={showSessionTokens}
                                onShowSessionTokensChange={setShowSessionTokens}
                                verboseLogging={verboseLogging}
                                onVerboseLoggingChange={(checked: boolean) => {
                                    setVerboseLogging(checked);
                                    realtime.sendVerboseLogging(checked);
                                    if (!checked && logToFile) {
                                        setLogToFile(false);
                                        realtime.sendLogToFile(false);
                                    }
                                }}
                                logToFile={logToFile}
                                onLogToFileChange={(checked: boolean) => {
                                    setLogToFile(checked);
                                    realtime.sendLogToFile(checked);
                                }}
                            />
                        </Suspense>
                        {authEnabled && (
                            <Button variant="ghost" size="icon" className="rounded-full" onClick={logout} title="Logout">
                                <LogOut className="h-4 w-4" />
                            </Button>
                        )}
                    </div>
                </div>

                {sessionIdentifiers && showSessionTokens && <SessionTokenPanel identifiers={sessionIdentifiers} history={tokenHistory} />}

                <BrandHero />

                <div className="grid grid-cols-1 gap-4 md:grid-cols-3 md:gap-8">
                    {/* Mobile Menu Button */}
                    <Sheet>
                        <SheetTrigger asChild>
                            <Button variant="outline" className="mb-4 flex w-full items-center justify-center md:hidden">
                                <Menu className="mr-2 h-4 w-4" />
                                View McDonald's Menu
                            </Button>
                        </SheetTrigger>
                        <SheetContent side="left" className="w-[300px] sm:w-[400px]">
                            <SheetHeader>
                                <SheetTitle>McDonald's Favorites</SheetTitle>
                            </SheetHeader>
                            <div className="h-[calc(100vh-4rem)] overflow-auto pr-4">
                                <MenuPanel />
                            </div>
                        </SheetContent>
                    </Sheet>

                    {/* Desktop Menu Panel */}
                    <Card className="hidden p-6 md:block">
                        <h2 className="mb-4 text-center font-semibold text-primary">McDonald's Favorites</h2>
                        <div className="h-[calc(100vh-13rem)] overflow-auto pr-4">
                            <MenuPanel />
                        </div>
                    </Card>

                    {/* Center Panel - Recording Button and Order Summary */}
                    <Card className="p-6 md:overflow-auto">
                        <div className="space-y-8">
                            <OrderSummary order={useDummyData ? dummyOrder : order} />
                            <div className="mb-4 flex flex-col items-center justify-center">
                                <Button
                                    onClick={onToggleListening}
                                    className={`h-12 w-60 border-none font-semibold shadow-lg transition-colors ${
                                        isRecording ? "bg-[#27251F] text-white hover:bg-[#3d3a32]" : "bg-[#DB0007] text-white hover:bg-[#a50005]"
                                    }`}
                                    aria-label={isRecording ? t("app.stopRecording") : t("app.startRecording")}
                                >
                                    {isRecording ? (
                                        <>
                                            <MicOff className="mr-2 h-4 w-4" />
                                            {t("app.stopConversation")}
                                        </>
                                    ) : (
                                        <>
                                            <Mic className="mr-2 h-6 w-6" />
                                        </>
                                    )}
                                </Button>
                                <StatusMessage isRecording={isRecording} />
                            </div>
                        </div>
                    </Card>

                    {/* Mobile Transcript Button */}
                    <Sheet>
                        <SheetTrigger asChild>
                            <Button variant="outline" className="mt-4 flex w-full items-center justify-center md:hidden">
                                <MessageSquare className="mr-2 h-4 w-4" />
                                Transcript
                            </Button>
                        </SheetTrigger>
                        <SheetContent side="right" className="w-[300px] sm:w-[400px]">
                            <SheetHeader>
                                <SheetTitle>Guest Conversation</SheetTitle>
                            </SheetHeader>
                            <div className="h-[calc(100vh-4rem)] overflow-auto pr-4">
                                <TranscriptPanel transcripts={useDummyData ? dummyTranscripts : transcripts} />
                            </div>
                        </SheetContent>
                    </Sheet>

                    {/* Desktop Transcript Panel */}
                    <Card className="hidden p-6 md:block">
                        <h2 className="mb-4 text-center font-semibold text-primary">Guest Conversation</h2>
                        <div className="h-[calc(100vh-13rem)] overflow-auto pr-4">
                            <TranscriptPanel transcripts={useDummyData ? dummyTranscripts : transcripts} />
                        </div>
                    </Card>
                </div>
            </div>
            <footer className="mx-auto mt-8 max-w-4xl space-y-2 text-center text-xs text-muted-foreground">
                <p className="text-base font-bold italic text-[#FFBC0D]">I'm Lovin' It™</p>
                <p className="font-semibold uppercase tracking-[0.35em] text-[#27251F]/80">{t("app.footer")}</p>
                <p className="text-[11px] leading-relaxed text-[#27251F]/80">
                    This is a demo application built with Azure AI services. It is not affiliated with, endorsed by, or sponsored by McDonald's Corporation. McDonald's, Big Mac, McNuggets, McFlurry, McCafé, and other brand names are trademarks of McDonald's Corporation.
                </p>
            </footer>
        </div>
    );
}

const BrandHero = memo(function BrandHero() {
    return (
        <section className="hero-card rounded-[32px] border border-white/40 bg-white/80 p-6 shadow-[0_25px_70px_rgba(39,37,31,0.18)] backdrop-blur-lg">
            <div className="flex flex-col gap-8 lg:flex-row lg:items-center">
                <div className="flex-1 space-y-5">
                    <div className="flex flex-wrap items-center gap-3">
                        <img src={mcdonaldsLogo} alt="McDonald's logo" className="h-20 w-auto drop-shadow-sm" loading="lazy" />
                        <span className="rounded-full bg-[#DB0007]/10 px-3 py-1 text-xs font-black uppercase tracking-[0.3em] text-[#DB0007]">
                            Voice Ordering Demo
                        </span>
                    </div>
                    <h1 className="text-4xl font-black leading-tight text-[#DB0007] sm:text-5xl">McDonald's ordering powered by Azure conversation intelligence</h1>
                    <p className="max-w-2xl text-base text-muted-foreground">
                        Recreate the McDonald's Drive-Thru experience with Big Macs, McNuggets, and crew member favorites styled after I'm Lovin' It™—now
                        voice activated with Azure OpenAI + Azure AI Search grounding.
                    </p>
                    <div className="grid gap-3 sm:grid-cols-3">
                        {heroHighlights.map(highlight => (
                            <HeroHighlightCard key={highlight.title} {...highlight} />
                        ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-[0.3em] text-muted-foreground">
                        <img src={azureLogo} alt="Microsoft Azure" className="h-6 w-auto" loading="lazy" />
                        <span>Azure OpenAI · Azure Speech · Azure AI Search</span>
                    </div>
                </div>
                <div className="relative flex flex-1 items-center justify-center">
                    <div className="absolute inset-0 -z-10 rounded-[32px] bg-gradient-to-br from-[#DB0007]/10 via-[#F5F0EB] to-[#FFBC0D]/15 opacity-80 blur-3xl"></div>
                    <div className="grid w-full gap-4 sm:grid-cols-2">
                        <div className="rounded-3xl border border-[#DB0007]/20 bg-white/90 p-4 shadow-[0_25px_45px_rgba(219,0,7,0.12)]">
                            <div className="mb-3 flex items-center gap-3">
                                <div className="rounded-2xl bg-[#DB0007]/10 p-3">
                                    <FriesArt />
                                </div>
                                <div>
                                    <p className="text-xs font-bold uppercase tracking-wide text-[#DB0007]">World Famous Fries</p>
                                    <p className="text-sm font-semibold text-[#27251F]">Golden & crispy</p>
                                </div>
                            </div>
                            <ul className="text-xs font-medium text-[#27251F]/80">
                                {heroCallouts.map(callout => (
                                    <li key={callout.label} className="flex items-center justify-between rounded-full bg-white/80 px-3 py-1">
                                        <span>{callout.label}</span>
                                        <span style={{ color: callout.accent }}>{callout.value}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div className="rounded-3xl border border-[#27251F]/25 bg-gradient-to-br from-[#27251F]/10 to-[#FFBC0D]/10 p-4 shadow-[0_25px_45px_rgba(39,37,31,0.15)]">
                            <div className="mb-3 flex items-center gap-3">
                                <div className="rounded-2xl bg-white/60 p-3">
                                    <BurgerArt />
                                </div>
                                <div>
                                    <p className="text-xs font-bold uppercase tracking-wide text-[#27251F]">Crew favorite</p>
                                    <p className="text-sm font-semibold text-[#27251F]">Big Mac®</p>
                                </div>
                            </div>
                            <div className="rounded-2xl bg-white/80 p-3 text-sm font-semibold text-[#27251F]">
                                <p>Two all-beef patties, special sauce, lettuce, cheese</p>
                                <p className="text-xs text-[#DB0007]">Perfect pairing: Large Fries & a McCafé</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
});

function HeroHighlightCard({ title, detail, tone }: { title: string; detail: string; tone: HighlightTone }) {
    const gradientMap: Record<HighlightTone, string> = {
        red: "from-[#DB0007] to-[#FF4D4D]",
        blue: "from-[#27251F] to-[#4a4740]",
        yellow: "from-[#FFBC0D] to-[#FFD54F]"
    };

    return (
        <div className={`rounded-2xl bg-gradient-to-br ${gradientMap[tone]} p-3 text-white shadow-[0_10px_25px_rgba(0,0,0,0.08)]`}>
            <p className="text-xs uppercase tracking-[0.25em] text-white/80">{title}</p>
            <p className="text-sm font-semibold leading-tight">{detail}</p>
        </div>
    );
}

const SessionTokenPanel = memo(function SessionTokenPanel({
    identifiers,
    history
}: {
    identifiers: SessionIdentifiersState;
    history: SessionIdentifiersState[];
}) {
    const [expanded, setExpanded] = useState(false);

    return (
        <div className="rounded-xl border border-white/30 bg-white/90 font-mono text-xs shadow-sm dark:border-white/10 dark:bg-[#27251F]/90">
            <button
                type="button"
                onClick={() => setExpanded(prev => !prev)}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-white/50 dark:hover:bg-white/5"
                aria-expanded={expanded}
                aria-label="Toggle session token history"
            >
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="font-semibold text-[#27251F] dark:text-[#FFBC0D]">Session:</span>
                    <span className="break-all text-[#27251F] dark:text-gray-200">
                        {identifiers.sessionToken || ""}
                    </span>
                    <span className="mx-1 text-[#27251F]/40 dark:text-gray-500">|</span>
                    <span className="whitespace-nowrap rounded-full bg-[#27251F]/10 px-1.5 py-0.5 font-semibold text-[#27251F] dark:bg-[#FFBC0D]/10 dark:text-[#FFBC0D]">
                        Round #{identifiers.roundTripIndex}
                    </span>
                </div>
                <motion.span
                    animate={{ rotate: expanded ? 180 : 0 }}
                    transition={{ duration: 0.2 }}
                    className="shrink-0 text-[#27251F]/60 dark:text-white/50"
                >
                    <ChevronDown size={18} />
                </motion.span>
            </button>

            <AnimatePresence initial={false}>
                {expanded && history.length > 0 && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.25, ease: "easeInOut" }}
                        className="overflow-hidden"
                    >
                        <div className="max-h-40 overflow-y-auto border-t border-white/30 px-3 py-2 dark:border-white/10">
                            <div className="space-y-1">
                                {history.map((entry, i) => (
                                    <div
                                        key={`${entry.roundTripIndex}-${entry.roundTripToken}-${i}`}
                                        className={`flex items-start gap-2 rounded px-2 py-1 ${i === 0 ? "bg-[#DB0007]/5 dark:bg-[#DB0007]/10" : ""}`}
                                    >
                                        <span className="w-16 shrink-0 font-semibold text-[#27251F] dark:text-[#FFBC0D]">
                                            Round #{entry.roundTripIndex}
                                        </span>
                                        <span className="break-all text-[#27251F]/60 dark:text-gray-400">
                                            {entry.roundTripToken || ""}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
});

function FriesArt() {
    return (
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none" role="img" aria-label="McDonald's fries illustration">
            <path d="M14 16h20l-3 24H17L14 16z" fill="#FFBC0D" stroke="#27251F" strokeWidth="2" />
            <path d="M12 16h24v4H12z" fill="#DB0007" />
            <rect x="18" y="8" width="2.5" height="14" rx="1" fill="#FFBC0D" stroke="#27251F" strokeWidth="0.5" />
            <rect x="22" y="6" width="2.5" height="16" rx="1" fill="#FFBC0D" stroke="#27251F" strokeWidth="0.5" />
            <rect x="26" y="9" width="2.5" height="13" rx="1" fill="#FFBC0D" stroke="#27251F" strokeWidth="0.5" />
            <rect x="30" y="10" width="2.5" height="12" rx="1" fill="#FFBC0D" stroke="#27251F" strokeWidth="0.5" />
            <rect x="15" y="10" width="2.5" height="12" rx="1" fill="#FFBC0D" stroke="#27251F" strokeWidth="0.5" />
        </svg>
    );
}

function BurgerArt() {
    return (
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none" role="img" aria-label="McDonald's burger illustration">
            <path d="M10 22c0-8 6-14 14-14s14 6 14 14H10z" fill="#FFBC0D" stroke="#27251F" strokeWidth="2" />
            <rect x="9" y="22" width="30" height="4" rx="1" fill="#328500" />
            <rect x="9" y="26" width="30" height="3" rx="1" fill="#DB0007" />
            <rect x="9" y="29" width="30" height="4" rx="1" fill="#C9CFD4" />
            <path d="M10 33c0 4 6 7 14 7s14-3 14-7H10z" fill="#FFBC0D" stroke="#27251F" strokeWidth="2" />
            <circle cx="16" cy="16" r="1" fill="#DB0007" />
            <circle cx="24" cy="13" r="1" fill="#DB0007" />
            <circle cx="32" cy="16" r="1" fill="#DB0007" />
        </svg>
    );
}

// Main app component with authentication wrapper
function App() {
    const { isAuthenticated, isLoading, authEnabled } = useAuth();

    if (isLoading) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <div className="text-center">
                    <div className="mx-auto mb-4 h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
                    <p className="text-lg">Loading...</p>
                </div>
            </div>
        );
    }

    if (!isAuthenticated && authEnabled) {
        return null; // Auth provider will handle redirect
    }

    return <McDonaldsApp />;
}

export default function RootApp() {
    return (
        <AuthProvider>
            <ThemeProvider>
                <DummyDataProvider>
                    <AzureSpeechProvider>
                        <App />
                    </AzureSpeechProvider>
                </DummyDataProvider>
            </ThemeProvider>
        </AuthProvider>
    );
}
