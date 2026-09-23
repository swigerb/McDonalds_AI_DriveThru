import "./status-message.css";
import { useTranslation } from "react-i18next";
import { memo } from "react";
import { useLocalMode } from "@/context/local-mode-context";
import type { RateLimitNotice } from "@/hooks/useRateLimitApology";

export type ConnectionNotice = "idle" | "lost" | "reconnecting" | "resumed" | "tapToResume" | "resumeRejected" | "superseded" | null;

type Properties = {
    isRecording: boolean;
    notice?: ConnectionNotice;
    /** Shown in place of "Conversation in progress" while the server retries a rate-limited reply. */
    busyNotice?: RateLimitNotice;
};

const NOTICE_KEYS: Record<Exclude<ConnectionNotice, null>, string> = {
    idle: "status.sessionEndedIdle",
    lost: "status.connectionLost",
    reconnecting: "status.reconnecting",
    resumed: "status.resumed",
    tapToResume: "status.resumedTapToContinue",
    resumeRejected: "status.resumeRejected",
    superseded: "status.superseded"
};

const BUSY_KEYS: Record<Exclude<RateLimitNotice, null>, string> = {
    busy: "status.rateLimited",
    final: "status.rateLimitedFinal"
};

export default memo(function StatusMessage({ isRecording, notice = null, busyNotice = null }: Properties) {
    const { t } = useTranslation();
    const { localMode } = useLocalMode();

    const modeIndicator = (
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
            localMode
                ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300"
                : "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300"
        }`}>
            {localMode ? "🔌 Local" : "☁️ Cloud"}
        </span>
    );

    if (!isRecording) {
        return (
            <div className="mb-4 mt-6 flex items-center gap-2">
                <p className="text-sm text-muted-foreground" aria-live="polite">
                    {t(notice ? NOTICE_KEYS[notice] : "status.notRecordingMessage")}
                </p>
                {modeIndicator}
            </div>
        );
    }

    return (
        <div className="flex items-center" aria-live="polite">
            <div className="listening-equalizer">
                {[...Array(4)].map((_, index) => (
                    <span key={index} className={`bar bar-${(index % 3) + 1}`} />
                ))}
            </div>
            <p className="mb-4 ml-2 mt-6 font-semibold text-primary">
                {t(busyNotice ? BUSY_KEYS[busyNotice] : notice === "resumed" ? NOTICE_KEYS.resumed : "status.conversationInProgress")}
            </p>
            <span className="mb-4 ml-2 mt-6">{modeIndicator}</span>
        </div>
    );
});
