import "./status-message.css";
import { useTranslation } from "react-i18next";
import { memo } from "react";
import { useLocalMode } from "@/context/local-mode-context";

type Properties = {
    isRecording: boolean;
};

export default memo(function StatusMessage({ isRecording }: Properties) {
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
                    {t("status.notRecordingMessage")}
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
                {t("status.conversationInProgress")}
            </p>
            <span className="mb-4 ml-2 mt-6">{modeIndicator}</span>
        </div>
    );
});
