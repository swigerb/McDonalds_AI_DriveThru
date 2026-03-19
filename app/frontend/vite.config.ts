import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    build: {
        outDir: "../backend/static",
        emptyOutDir: true,
        sourcemap: false,
        chunkSizeWarningLimit: 1000,
        target: "es2020",
        minify: "esbuild",
        cssMinify: true,
        rollupOptions: {
            output: {
                manualChunks: {
                    "react-vendor": ["react", "react-dom"],
                    "ui-vendor": [
                        "@radix-ui/react-dialog",
                        "@radix-ui/react-select",
                        "@radix-ui/react-slider",
                        "@radix-ui/react-slot",
                        "@radix-ui/react-label",
                        "@radix-ui/react-tooltip"
                    ],
                    "i18n": ["i18next", "react-i18next", "i18next-browser-languagedetector", "i18next-http-backend"],
                    "motion": ["framer-motion"]
                },
                assetFileNames: "assets/[name]-[hash][extname]",
                chunkFileNames: "js/[name]-[hash].js",
                entryFileNames: "js/[name]-[hash].js"
            }
        }
    },
    resolve: {
        preserveSymlinks: true,
        alias: {
            "@": path.resolve(__dirname, "./src")
        }
    },
    server: {
        proxy: {
            "/realtime": {
                target: "ws://localhost:8000",
                ws: true,
                rewriteWsOrigin: true
            }
        }
    },
    test: {
        globals: true,
        environment: "jsdom",
        setupFiles: "./src/test/setup.ts",
        css: true,
        coverage: {
            provider: "v8",
            reporter: ["text", "lcov"],
            include: ["src/components/ui/order-summary.tsx", "src/components/ui/status-message.tsx"]
        }
    }
});
