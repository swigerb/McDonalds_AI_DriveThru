import { X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useToast, dismissToast } from "./use-toast";

export function Toaster() {
    const toasts = useToast();

    return (
        <div className="fixed top-4 right-4 z-[100] flex max-w-sm flex-col gap-2">
            <AnimatePresence>
                {toasts.map(t => (
                    <motion.div
                        key={t.id}
                        initial={{ opacity: 0, x: 40, scale: 0.95 }}
                        animate={{ opacity: 1, x: 0, scale: 1 }}
                        exit={{ opacity: 0, x: 40, scale: 0.95 }}
                        transition={{ type: "spring", stiffness: 400, damping: 30 }}
                        role="alert"
                        className={`flex items-start gap-3 rounded-lg border p-4 shadow-lg backdrop-blur-sm ${
                            t.variant === "error"
                                ? "border-[#DB0007]/30 bg-white/95 dark:border-red-700/40 dark:bg-gray-900/95"
                                : "border-[#FFBC0D]/40 bg-white/95 dark:border-amber-600/40 dark:bg-gray-900/95"
                        }`}
                    >
                        <span className="mt-0.5 text-lg leading-none" aria-hidden="true">
                            {t.variant === "error" ? "🔴" : "⚠️"}
                        </span>
                        <p
                            className={`flex-1 text-sm font-semibold ${
                                t.variant === "error"
                                    ? "text-[#DB0007] dark:text-red-400"
                                    : "text-[#92400e] dark:text-amber-300"
                            }`}
                        >
                            {t.message}
                        </p>
                        <button
                            onClick={() => dismissToast(t.id)}
                            className="rounded-full p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                            aria-label="Dismiss notification"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </motion.div>
                ))}
            </AnimatePresence>
        </div>
    );
}
