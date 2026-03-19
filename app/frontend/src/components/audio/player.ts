export class Player {
    private playbackNode: AudioWorkletNode | null = null;
    private audioContext: AudioContext | null = null;
    private workletReady = false;
    private drainWaiters: Array<() => void> = [];

    async init(sampleRate: number) {
        // Reuse existing AudioContext to avoid expensive re-creation
        if (!this.audioContext || this.audioContext.state === "closed") {
            this.audioContext = new AudioContext({ sampleRate });
            this.workletReady = false;
        }

        if (this.audioContext.state === "suspended") {
            await this.audioContext.resume();
        }

        // Clear any queued audio from previous session
        if (this.playbackNode) {
            this.playbackNode.port.postMessage(null);
            this.playbackNode.disconnect();
            this.playbackNode = null;
        }

        if (!this.workletReady) {
            await this.audioContext.audioWorklet.addModule("audio-playback-worklet.js");
            this.workletReady = true;
        }

        this.playbackNode = new AudioWorkletNode(this.audioContext, "audio-playback-worklet");
        this.playbackNode.port.onmessage = event => {
            if (event?.data?.type === "drained") {
                const waiters = this.drainWaiters;
                this.drainWaiters = [];
                waiters.forEach(resolve => resolve());
            }
        };
        this.playbackNode.connect(this.audioContext.destination);
    }

    play(buffer: Int16Array) {
        if (this.playbackNode) {
            this.playbackNode.port.postMessage(buffer);
        }
    }

    waitForDrain(timeoutMs = 2000): Promise<boolean> {
        if (!this.playbackNode) {
            return Promise.resolve(false);
        }

        return new Promise(resolve => {
            const timer = window.setTimeout(() => {
                resolve(false);
            }, timeoutMs);

            this.drainWaiters.push(() => {
                window.clearTimeout(timer);
                resolve(true);
            });
        });
    }

    stop() {
        if (this.playbackNode) {
            this.playbackNode.port.postMessage(null);
        }
    }
}
