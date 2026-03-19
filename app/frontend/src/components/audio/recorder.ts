export class Recorder {
    onDataAvailable: (buffer: Iterable<number>) => void;
    private audioContext: AudioContext | null = null;
    private mediaStream: MediaStream | null = null;
    private mediaStreamSource: MediaStreamAudioSourceNode | null = null;
    private workletNode: AudioWorkletNode | null = null;
    private workletReady = false;

    public constructor(onDataAvailable: (buffer: Iterable<number>) => void) {
        this.onDataAvailable = onDataAvailable;
    }

    async start(stream: MediaStream) {
        try {
            // Reuse existing AudioContext instead of recreating (expensive operation)
            if (!this.audioContext || this.audioContext.state === "closed") {
                this.audioContext = new AudioContext({ sampleRate: 24000 });
                this.workletReady = false;
            }

            if (this.audioContext.state === "suspended") {
                await this.audioContext.resume();
            }

            if (!this.workletReady) {
                await this.audioContext.audioWorklet.addModule("./audio-processor-worklet.js");
                this.workletReady = true;
            }

            this.mediaStream = stream;
            this.mediaStreamSource = this.audioContext.createMediaStreamSource(this.mediaStream);

            this.workletNode = new AudioWorkletNode(this.audioContext, "audio-processor-worklet");
            this.workletNode.port.onmessage = event => {
                this.onDataAvailable(event.data.buffer);
            };

            this.mediaStreamSource.connect(this.workletNode);
            this.workletNode.connect(this.audioContext.destination);
        } catch (error) {
            this.stop();
        }
    }

    async stop() {
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        // Disconnect nodes but keep AudioContext alive for reuse
        if (this.workletNode) {
            this.workletNode.disconnect();
            this.workletNode = null;
        }
        if (this.mediaStreamSource) {
            this.mediaStreamSource.disconnect();
            this.mediaStreamSource = null;
        }
    }
}
