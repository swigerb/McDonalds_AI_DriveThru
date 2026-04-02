import { useState, useEffect } from "react";

export type ToastVariant = "error" | "warning";

export type Toast = {
    id: string;
    message: string;
    variant: ToastVariant;
};

let toasts: Toast[] = [];
let listeners: Array<() => void> = [];
let nextId = 0;

function emit() {
    listeners.forEach(fn => fn());
}

/** Show a persistent toast. Deduplicates by message — won't stack identical toasts. */
export function toast(message: string, variant: ToastVariant = "error") {
    if (toasts.some(t => t.message === message)) return;
    toasts = [...toasts, { id: String(++nextId), message, variant }];
    emit();
}

/** Dismiss a single toast by ID. */
export function dismissToast(id: string) {
    toasts = toasts.filter(t => t.id !== id);
    emit();
}

/** Dismiss all toasts (e.g. when connection recovers). */
export function dismissAllToasts() {
    if (toasts.length === 0) return;
    toasts = [];
    emit();
}

/** React hook — subscribes to the global toast list. */
export function useToast() {
    const [, rerender] = useState(0);

    useEffect(() => {
        const listener = () => rerender(c => c + 1);
        listeners.push(listener);
        return () => {
            listeners = listeners.filter(l => l !== listener);
        };
    }, []);

    return toasts;
}
