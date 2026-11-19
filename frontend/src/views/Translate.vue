<template>
  <div class="translate">
    <section class="stage">
      <video ref="videoRef" class="video" autoplay playsinline muted></video>
      <div class="stage-empty" v-if="!cameraOn">点击「开启摄像头」开始</div>
      <div class="hud">
        <div>FPS <b>{{ fps }}</b></div>
        <div>动作 <b :class="'state-' + detectorState">{{ stateText }}</b></div>
        <div>diff <b>{{ diff }}</b></div>
      </div>
    </section>

    <aside class="panel">
      <el-card shadow="never" class="card">
        <div class="row">
          <el-select v-model="sceneId" size="small" style="width: 140px" @change="onSceneChange">
            <el-option v-for="scene in scenes" :key="scene.id" :label="scene.name" :value="scene.id" />
          </el-select>
          <el-button size="small" type="primary" @click="toggleCamera">{{ cameraOn ? '关闭摄像头' : '开启摄像头' }}</el-button>
          <el-switch v-model="autoMode" active-text="自动检测" @change="onModeChange" />
        </div>
        <div class="row thresholds" v-if="autoMode">
          <span class="label">T_start</span>
          <el-slider v-model="tStart" :min="2" :max="40" size="small" style="flex: 1" @change="applyThresholds" />
          <span class="value">{{ tStart }}</span>
        </div>
        <div class="row thresholds" v-if="autoMode">
          <span class="label">T_stop</span>
          <el-slider v-model="tStop" :min="1" :max="30" size="small" style="flex: 1" @change="applyThresholds" />
          <span class="value">{{ tStop }}</span>
        </div>
        <div class="row" v-if="!autoMode">
          <el-button size="small" :type="manualRecording ? 'danger' : 'success'" @click="toggleManualRecord">
            {{ manualRecording ? '结束录制（空格）' : '开始录制（空格）' }}
          </el-button>
          <span class="hint">手动模式：录 2 秒左右的动作</span>
        </div>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><div class="card-title">手语词序列</div></template>
        <div class="words">
          <el-tag v-for="(word, index) in words" :key="index" closable size="large" @close="removeWord(index)">{{ word.text }}</el-tag>
          <span class="empty" v-if="!words.length">等待识别…</span>
        </div>
        <div class="candidates" v-if="candidates.length">
          <span class="hint">置信度较低，请点选：</span>
          <el-button v-for="candidate in candidates" :key="candidate" size="small" @click="pickCandidate(candidate)">{{ candidate }}</el-button>
        </div>
        <div class="row" style="margin-top: 10px">
          <el-button size="small" type="primary" :disabled="!words.length" @click="composeNow">完成并造句</el-button>
          <el-button size="small" :disabled="!words.length" @click="clearWords">清空</el-button>
          <span class="hint" v-if="composing">流式造句中…</span>
        </div>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><div class="card-title">翻译结果</div></template>
        <div class="sentence">{{ sentence || '—' }}</div>
        <div class="alternative" v-if="alternative">备选：{{ alternative }}</div>
        <div class="row" style="margin-top: 10px">
          <el-button size="small" type="success" :disabled="!sentence || speaking" @click="speak(sentence)">播放语音</el-button>
          <span class="hint" v-if="speaking">正在播放…</span>
          <span class="hint" v-if="mockTip">{{ mockTip }}</span>
        </div>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><div class="card-title">反向沟通（健听人说话）</div></template>
        <div class="row">
          <el-button size="small" :type="recordingAudio ? 'danger' : 'primary'" @click="toggleAudioRecord">
            {{ recordingAudio ? '停止并识别' : '开始说话' }}
          </el-button>
          <span class="hint">按一次开始，再按一次结束</span>
        </div>
        <div class="other" v-if="otherText">对方说：{{ otherText }}</div>
        <div class="replies" v-if="replies.length">
          <el-button v-for="reply in replies" :key="reply" size="small" @click="sendQuickReply(reply)">{{ reply }}</el-button>
        </div>
      </el-card>
    </aside>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { MotionDetector } from "../utils/motion";
import { captureFrame, composeGrid } from "../utils/grid";
import { AudioRecorder } from "../utils/recorder";

const videoRef = ref(null);
const scenes = ref([]);
const sceneId = ref("hospital");
const sceneName = ref("医院就诊");

const cameraOn = ref(false);
const autoMode = ref(true);
const detectorState = ref("IDLE");
const diff = ref("0.0");
const fps = ref("0");
const tStart = ref(10);
const tStop = ref(6);

const words = ref([]);
const candidates = ref([]);
const sentence = ref("");
const alternative = ref("");
const composing = ref(false);
const speaking = ref(false);
const mockTip = ref("");
const history = ref([]);

const manualRecording = ref(false);
const recordingAudio = ref(false);
const otherText = ref("");
const replies = ref([]);

let detector = null;
let rafId = 0;
let stream = null;
let frameCount = 0;
let fpsAt = 0;
let manualTimer = 0;
let manualFrames = [];
let recorder = null;
let audioEl = null;

const stateText = computed(function () {
  const map = { IDLE: "空闲", SIGNING: "打手语中", COMMITTING: "等待句子结束" };
  return map[detectorState.value] || detectorState.value;
});

function formatDiff(value) {
  return (Math.round(value * 10) / 10).toFixed(1);
}

/* ---------------- 初始化 ---------------- */
onMounted(async function () {
  try {
    scenes.value = await api.getScenes();
  } catch (error) {
    ElMessage.error("加载场景失败：" + error.message);
  }
  detector = new MotionDetector({ tStart: tStart.value, tStop: tStop.value });
  detector.onStateChange = function (state) {
    detectorState.value = state;
  };
  detector.onActionEnd = function (frames) {
    if (!autoMode.value) return;
    recognizeFrames(frames);
  };
  detector.onSentenceEnd = function () {
    if (!autoMode.value) return;
    if (words.value.length > 0) composeNow();
  };
  rafId = requestAnimationFrame(loop);
  window.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(function () {
  cancelAnimationFrame(rafId);
  window.removeEventListener("keydown", onKeydown);
  stopCamera();
  stopManualRecord();
  if (recorder) recorder.close();
  if (audioEl) audioEl.pause();
});

/* ---------------- 主循环 ---------------- */
function loop(now) {
  rafId = requestAnimationFrame(loop);
  const video = videoRef.value;
  if (!cameraOn.value || !video || !detector) return;
  detector.push(video, now);
  diff.value = formatDiff(detector.diff);
  frameCount += 1;
  if (!fpsAt) fpsAt = now;
  if (now - fpsAt >= 500) {
    fps.value = String(Math.round((frameCount * 1000) / (now - fpsAt)));
    frameCount = 0;
    fpsAt = now;
  }
}

/* ---------------- 摄像头 ---------------- */
async function toggleCamera() {
  if (cameraOn.value) {
    stopCamera();
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
      audio: false,
    });
    const video = videoRef.value;
    video.srcObject = stream;
    await video.play();
    cameraOn.value = true;
    detector.reset();
    if (!fpsAt) fpsAt = performance.now();
  } catch (error) {
    ElMessage.error("摄像头打开失败：" + (error.message || error) + "（需要 HTTPS 或 localhost）");
  }
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach(function (track) {
      track.stop();
    });
    stream = null;
  }
  if (videoRef.value) videoRef.value.srcObject = null;
  cameraOn.value = false;
  if (detector) detector.reset();
}

/* ---------------- 识别 ---------------- */
async function recognizeFrames(frames) {
  if (!frames || frames.length < 2) return;
  const image = composeGrid(frames, { frameWidth: 320, frameHeight: 240 });
  await recognizeImage(image);
}

async function recognizeImage(image) {
  try {
    const result = await api.recognize(image, sceneId.value);
    mockTip.value = result.mock ? "当前为 Mock 识别（未配置 MaaS 密钥）" : "";
    const recognized = result.words || [];
    if (!recognized.length) {
      ElMessage.warning("这个动作没有识别出词，请重打或手动选择");
      return;
    }
    const confidence = Number(result.confidence || 0);
    if (confidence >= 0.6) {
      addWord(recognized[0]);
    } else {
      const options = [recognized[0]].concat(recognized.slice(1));
      const alternates = result.alternatives || [];
      alternates.forEach(function (group) {
        group.forEach(function (word) {
          if (options.indexOf(word) === -1) options.push(word);
        });
      });
      candidates.value = options;
      ElMessage.info("置信度较低（" + Math.round(confidence * 100) + "%），请点选候选词");
    }
  } catch (error) {
    ElMessage.error("识别失败：" + error.message);
  }
}

function addWord(text) {
  if (!text) return;
  words.value.push({ text: text });
  candidates.value = [];
}

function pickCandidate(text) {
  addWord(text);
}

function removeWord(index) {
  words.value.splice(index, 1);
}

function clearWords() {
  words.value = [];
  candidates.value = [];
  sentence.value = "";
  alternative.value = "";
}

function onSceneChange() {
  const scene = scenes.value.find(function (item) {
    return item.id === sceneId.value;
  });
  sceneName.value = scene ? scene.name : sceneId.value;
  clearWords();
}

/* ---------------- 流式造句 ---------------- */
async function composeNow() {
  if (!words.value.length || composing.value) return;
  composing.value = true;
  sentence.value = "";
  alternative.value = "";
  const wordTexts = words.value.map(function (item) {
    return item.text;
  });
  await api.composeStream(wordTexts, sceneId.value, history.value, {
    onDelta: function (text) {
      sentence.value += text;
    },
    onDone: function (fullSentence, alt) {
      composing.value = false;
      if (fullSentence) sentence.value = fullSentence;
      alternative.value = alt || "";
      history.value.push({ role: "me", text: sentence.value });
      speak(sentence.value);
    },
    onError: function (error) {
      composing.value = false;
      ElMessage.error("造句失败：" + error.message);
    },
  });
}

/* ---------------- 语音播放 ---------------- */
async function speak(text) {
  if (!text || speaking.value) return;
  speaking.value = true;
  try {
    const result = await api.tts(text);
    if (audioEl) audioEl.pause();
    audioEl = new Audio("data:audio/wav;base64," + result.audio);
    audioEl.onended = function () {
      speaking.value = false;
    };
    audioEl.onerror = function () {
      speaking.value = false;
    };
    await audioEl.play();
  } catch (error) {
    speaking.value = false;
    ElMessage.error("语音播放失败：" + error.message);
  }
}

/* ---------------- 手动录制 ---------------- */
function toggleManualRecord() {
  if (manualRecording.value) {
    stopManualRecord();
    return;
  }
  if (!cameraOn.value) {
    ElMessage.warning("请先开启摄像头");
    return;
  }
  manualFrames = [];
  manualRecording.value = true;
  manualTimer = window.setInterval(function () {
    const video = videoRef.value;
    if (video && video.readyState >= 2) manualFrames.push(captureFrame(video));
  }, 200);
}

function stopManualRecord() {
  if (!manualRecording.value) return;
  manualRecording.value = false;
  if (manualTimer) {
    clearInterval(manualTimer);
    manualTimer = 0;
  }
  const frames = manualFrames;
  manualFrames = [];
  if (frames.length >= 2) recognizeFrames(frames);
}

function applyThresholds() {
  if (detector) detector.setThresholds(tStart.value, tStop.value);
}

function onModeChange(value) {
  if (detector) detector.reset();
  if (value && manualRecording.value) stopManualRecord();
}

function onKeydown(event) {
  if (event.code !== "Space" || event.repeat) return;
  const tag = (event.target && event.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (autoMode.value) return;
  event.preventDefault();
  toggleManualRecord();
}

/* ---------------- 反向语音 ---------------- */
async function toggleAudioRecord() {
  if (!recordingAudio.value) {
    recorder = new AudioRecorder();
    try {
      await recorder.start();
      recordingAudio.value = true;
    } catch (error) {
      ElMessage.error("麦克风打开失败：" + (error.message || error));
    }
    return;
  }
  recordingAudio.value = false;
  try {
    const result = await recorder.stop();
    recorder.close();
    const asr = await api.asr(result.wavBase64);
    otherText.value = asr.text || "（没有听清）";
    history.value.push({ role: "other", text: otherText.value });
    const quick = await api.quickReply(otherText.value, sceneId.value);
    replies.value = quick.replies || [];
  } catch (error) {
    ElMessage.error("语音识别失败：" + error.message);
  }
}

async function sendQuickReply(text) {
  history.value.push({ role: "me", text: text });
  await speak(text);
}
</script>

<style scoped>
.translate {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(360px, 1fr);
  gap: 16px;
}

.stage {
  position: relative;
  background: #000;
  border: 1px solid #26304b;
  border-radius: 14px;
  overflow: hidden;
  aspect-ratio: 16 / 9;
  min-height: 320px;
}

.video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: scaleX(-1);
}

.stage-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8b96b3;
  font-size: 15px;
}

.hud {
  position: absolute;
  right: 10px;
  top: 10px;
  background: rgba(10, 16, 32, 0.82);
  border: 1px solid #26304b;
  border-radius: 10px;
  padding: 8px 12px;
  font-family: Consolas, monospace;
  font-size: 12px;
  color: #8b96b3;
  line-height: 1.8;
}

.hud b {
  color: #38e8d0;
}

.state-SIGNING {
  color: #ffd166 !important;
}

.state-COMMITTING {
  color: #ff7ac6 !important;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card {
  background: #141a2e;
  border-color: #26304b;
}

.card :deep(.el-card__header) {
  border-bottom-color: #26304b;
  padding: 10px 14px;
}

.card :deep(.el-card__body) {
  padding: 12px 14px;
}

.card-title {
  font-size: 13px;
  color: #8b96b3;
  letter-spacing: 1px;
}

.row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.thresholds {
  margin-top: 8px;
}

.label {
  width: 52px;
  font-size: 12px;
  color: #8b96b3;
  font-family: Consolas, monospace;
}

.value {
  width: 22px;
  text-align: right;
  font-family: Consolas, monospace;
  color: #38e8d0;
}

.words {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 34px;
  align-items: center;
}

.empty {
  color: #5b6584;
  font-size: 13px;
}

.candidates {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: #ffd166;
}

.sentence {
  font-size: 22px;
  font-weight: 600;
  min-height: 34px;
  color: #e8ecf8;
}

.alternative {
  margin-top: 6px;
  font-size: 13px;
  color: #8b96b3;
}

.other {
  margin-top: 10px;
  font-size: 14px;
  color: #ffd166;
}

.replies {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.hint {
  font-size: 12px;
  color: #5b6584;
}

@media (max-width: 1100px) {
  .translate {
    grid-template-columns: 1fr;
  }
}
</style>
