/** 录音 -> 16k 单声道 wav base64（对应《工程实现方案》§3.7）。 */

export class AudioRecorder {
  constructor() {
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
  }

  async start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("当前浏览器不支持麦克风采集");
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream);
    const self = this;
    this.recorder.ondataavailable = function (event) {
      if (event.data && event.data.size > 0) self.chunks.push(event.data);
    };
    this.recorder.start();
  }

  async stop() {
    if (!this.recorder) throw new Error("还没有开始录音");
    const recorder = this.recorder;
    const chunks = this.chunks;
    const mimeType = recorder.mimeType || "audio/webm";
    return new Promise(function (resolve, reject) {
      recorder.onstop = async function () {
        try {
          const blob = new Blob(chunks, { type: mimeType });
          const wavBase64 = await blobToWavBase64(blob);
          resolve({ wavBase64: wavBase64 });
        } catch (error) {
          reject(error);
        }
      };
      recorder.stop();
    }).finally(function () {
      if (window.__signbridgeRecorderStream) {
        /* noop */
      }
    });
  }

  close() {
    if (this.stream) {
      this.stream.getTracks().forEach(function (track) {
        track.stop();
      });
      this.stream = null;
    }
    this.recorder = null;
  }
}

async function blobToWavBase64(blob) {
  const arrayBuffer = await blob.arrayBuffer();
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const decodeContext = new AudioContextClass();
  const decoded = await decodeContext.decodeAudioData(arrayBuffer.slice(0));
  await decodeContext.close();

  const targetRate = 16000;
  const frames = Math.max(1, Math.ceil(decoded.duration * targetRate));
  const OfflineContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const offline = new OfflineContext(1, frames, targetRate);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start(0);
  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);
  const wavBuffer = encodeWav(samples, targetRate);
  return arrayBufferToBase64(wavBuffer);
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return buffer;
}

function writeAscii(view, offset, text) {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}
