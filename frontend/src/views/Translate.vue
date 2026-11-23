<template>
  <div class="translate" :class="{ 'big-mode': bigSubtitle }">
    <div class="left-col">
      <section class="stage">
        <video ref="videoRef" class="video" autoplay playsinline muted></video>
        <canvas ref="overlayRef" class="overlay"></canvas>
        <div class="stage-empty" v-if="!cameraOn">点击「开启摄像头」开始</div>
        <div class="hud">
          <div>FPS <b>{{ fps }}</b></div>
          <div>动作 <b :class="'state-' + detectorState">{{ stateText }}</b></div>
          <div>diff <b>{{ diff }}</b></div>
          <div>手部 <b>{{ handStatusText }}</b></div>
          <div>ROI <b>{{ roiModeText }}</b></div>
        </div>
      </section>

      <el-card shadow="never" class="card history-card">
        <template #header>
          <div class="card-header-row">
            <span class="card-title">对话记录（{{ history.length }}）</span>
            <div class="row" v-if="history.length">
              <el-button size="small" text type="primary" @click="exportHistory">导出记录</el-button>
              <el-button size="small" text @click="clearHistory">清空记录</el-button>
            </div>
          </div>
        </template>
        <div v-if="!history.length" class="empty">暂无对话记录，打手语或点击反向沟通开始交流。</div>
        <div v-else class="history-list">
          <div v-for="(item, idx) in history" :key="idx" :class="['history-item', 'role-' + item.role]">
            <div class="history-meta">
              <span class="history-role">{{ item.role === 'other' ? '对方（语音）' : '我（手语）' }}</span>
              <span class="history-time">{{ item.time }}</span>
              <el-tag v-if="item.emergency" type="danger" size="small" effect="dark">紧急求助</el-tag>
              <el-tag v-if="item.guardTriggered" type="warning" size="small">护栏已介入</el-tag>
              <el-tag v-if="item.needConfirm && !item.emergency" type="info" size="small">待确认</el-tag>
            </div>
            <div class="history-body">
              <span class="history-text">{{ item.text }}</span>
              <el-button size="small" text type="primary" @click="speak(item.text)">朗读</el-button>
            </div>
            <div v-if="item.words && item.words.length" class="history-words">
              手语词：{{ item.words.join(' · ') }}
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <aside class="panel">
      <el-card shadow="never" class="card">
        <div class="row">
          <el-select v-model="sceneId" size="small" style="width: 126px" @change="onSceneChange">
            <el-option v-for="scene in scenes" :key="scene.id" :label="scene.name" :value="scene.id" />
          </el-select>
          <el-button size="small" type="primary" @click="toggleCamera">{{ cameraOn ? '关闭摄像头' : '开启摄像头' }}</el-button>
          <el-switch v-model="autoMode" active-text="自动检测" @change="onModeChange" />
          <el-switch v-model="roiEnabled" active-text="ROI裁切" @change="onRoiChange" />
          <el-button size="small" type="danger" @click="triggerEmergency">紧急求助</el-button>
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
        <div class="row" v-if="!autoMode" style="margin-top: 8px">
          <el-button size="small" :type="manualRecording ? 'danger' : 'success'" @click="toggleManualRecord">
            {{ manualRecording ? '结束录制（空格）' : '开始录制（空格）' }}
          </el-button>
          <span class="hint">手动模式：录 2 秒左右的动作</span>
        </div>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header>
          <div class="card-header-row">
            <span class="card-title">手语词序列</span>
            <div class="row">
              <el-button v-if="lastGridUrl" size="small" text @click="showGridPreview = !showGridPreview">
                {{ showGridPreview ? '收起拼图' : '查看最近拼图' }}
              </el-button>
              <el-button size="small" text @click="vocabOpen = !vocabOpen">
                {{ vocabOpen ? '收起词表' : '展开词表' }}
              </el-button>
            </div>
          </div>
        </template>
        <div class="words">
          <el-tag v-for="(word, index) in words" :key="index" closable size="large" @close="removeWord(index)">{{ word.text }}</el-tag>
          <span class="empty" v-if="!words.length">等待识别或下方点选词表…</span>
        </div>
        <div class="candidates" v-if="candidates.length">
          <span class="hint">置信度较低，请点选：</span>
          <el-button v-for="candidate in candidates" :key="candidate" size="small" @click="pickCandidate(candidate)">{{ candidate }}</el-button>
        </div>
        <div class="row" style="margin-top: 10px">
          <el-button size="small" type="primary" :disabled="!words.length || composing" @click="composeNow()">完成并造句</el-button>
          <el-button size="small" :disabled="!words.length" @click="clearWords">清空</el-button>
          <span class="hint" v-if="pendingRecognitions > 0">识别中（{{ pendingRecognitions }}）…</span>
          <span class="hint" v-if="pendingSentenceEnd">等待识别完成后自动造句…</span>
          <span class="hint" v-if="composing">流式造句中…</span>
        </div>

        <div class="grid-preview" v-if="showGridPreview && lastGridUrl">
          <div class="hint">最近上传 6 帧拼图（{{ lastRoiSource }}）：</div>
          <img :src="lastGridUrl" alt="6帧时序拼图" class="grid-img" />
        </div>

        <div class="vocab-box" v-if="vocabOpen && currentVocab.length">
          <div class="vocab-title">词表点选（兜底 · 悬停可看动作要领）</div>
          <div class="vocab-list">
            <el-button
              v-for="item in currentVocab"
              :key="item.word"
              size="small"
              class="vocab-btn"
              :title="item.desc"
              @click="addWord(item.word)"
            >
              {{ item.word }}
            </el-button>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header>
          <div class="card-header-row">
            <span class="card-title">翻译结果</span>
            <el-switch v-model="bigSubtitle" active-text="大字模式" inline-prompt />
          </div>
        </template>
        <el-alert
          v-if="isEmergency"
          title="紧急求助旁路已触发：跳过大模型直接播报标准化求助语音"
          type="error"
          :closable="false"
          show-icon
          style="margin-bottom: 8px"
        />
        <el-alert
          v-if="guardTriggered"
          :title="'医疗/事实护栏已介入（' + guardReason + '），已回退安全表达'"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 8px"
        />
        <div class="sentence">{{ sentence || '—' }}</div>
        <div class="confirm-row" v-if="needConfirm && !isEmergency">
          <el-tag type="warning" size="small">涉及医疗/高风险业务，请双方确认含义</el-tag>
        </div>
        <div class="alternative" v-if="alternative">
          备选：{{ alternative }}
          <el-button size="small" text type="primary" @click="useAlternative(alternative)">改用并播报</el-button>
        </div>
        <div class="row" style="margin-top: 10px">
          <el-button size="small" type="success" :disabled="!sentence" @click="speak(sentence)">播放语音</el-button>
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
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { MotionDetector } from "../utils/motion";
import { captureFrame, composeGridWithMeta } from "../utils/grid";
import { HandTracker } from "../utils/handTracker";
import { computeSegmentRoi } from "../utils/roi";
import { AudioRecorder } from "../utils/recorder";

const videoRef = ref(null);
const overlayRef = ref(null);
const scenes = ref([]);
const sceneId = ref("hospital");
const sceneName = ref("医院就诊");

const cameraOn = ref(false);
const autoMode = ref(true);
const roiEnabled = ref(true);
const detectorState = ref("IDLE");
const diff = ref("0.0");
const fps = ref("0");
const tStart = ref(10);
const tStop = ref(6);

const trackerStatus = ref("idle");
const handDetected = ref(false);
const liveRoi = ref(null);

const words = ref([]);
const candidates = ref([]);
const vocabOpen = ref(true);
const showGridPreview = ref(false);
const lastGridUrl = ref("");
const lastRoiSource = ref("");

const sentence = ref("");
const alternative = ref("");
const composing = ref(false);
const speaking = ref(false);
const mockTip = ref("");
const isEmergency = ref(false);
const needConfirm = ref(false);
const guardTriggered = ref(false);
const guardReason = ref("");

const pendingRecognitions = ref(0);
const pendingSentenceEnd = ref(false);
const history = ref([]);

/* ---------------- 对话记录持久化（localStorage，刷新不丢） ---------------- */
const HISTORY_KEY = "signbridge.history.v1";
const HISTORY_LIMIT = 200;
let historySaveTimer = 0;

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return;
    const list = JSON.parse(raw);
    if (Array.isArray(list)) history.value = list.slice(-HISTORY_LIMIT);
  } catch (error) {
    /* 数据损坏时忽略，从空记录开始 */
  }
}

watch(
  history,
  function () {
    if (historySaveTimer) clearTimeout(historySaveTimer);
    historySaveTimer = window.setTimeout(function () {
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value.slice(-HISTORY_LIMIT)));
      } catch (error) {
        /* 存储满（如长期未清理）时静默失败，不阻塞交互 */
      }
    }, 300);
  },
  { deep: true },
);

/* ---------------- 大字模式（面对面共看一台设备时用） ---------------- */
const BIG_KEY = "signbridge.big_subtitle";
const bigSubtitle = ref(false);
try {
  bigSubtitle.value = localStorage.getItem(BIG_KEY) === "1";
} catch (error) {
  /* ignore */
}
watch(bigSubtitle, function (value) {
  try {
    localStorage.setItem(BIG_KEY, value ? "1" : "0");
  } catch (error) {
    /* ignore */
  }
});

const manualRecording = ref(false);
const recordingAudio = ref(false);
const otherText = ref("");
const replies = ref([]);

let detector = null;
let tracker = null;
let rafId = 0;
let stream = null;
let frameCount = 0;
let fpsAt = 0;
let manualTimer = 0;
let manualFrames = [];
let recorder = null;
let audioEl = null;
let speakSeq = 0;

const currentVocab = computed(function () {
  const found = scenes.value.find(function (item) {
    return item.id === sceneId.value;
  });
  return found && Array.isArray(found.vocab) ? found.vocab : [];
});

const stateText = computed(function () {
  const map = { IDLE: "空闲", SIGNING: "打手语中", COMMITTING: "等待句子结束" };
  return map[detectorState.value] || detectorState.value;
});

const handStatusText = computed(function () {
  if (!cameraOn.value) return "未启用";
  if (trackerStatus.value === "loading") return "加载中";
  if (trackerStatus.value === "fallback") return "降级帧差";
  if (trackerStatus.value !== "ready") return "未就绪";
  return handDetected.value ? "检测到" : "未检测到";
});

const roiModeText = computed(function () {
  if (!roiEnabled.value) return "全幅";
  return trackerStatus.value === "ready" ? "已裁切" : "居中4:3";
});

function formatDiff(value) {
  return (Math.round(value * 10) / 10).toFixed(1);
}

function formatNowTime() {
  const now = new Date();
  return [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map(function (n) {
      return String(n).padStart(2, "0");
    })
    .join(":");
}

function syncRequireHands() {
  if (!detector) return;
  detector.setRequireHands(autoMode.value && roiEnabled.value && trackerStatus.value === "ready");
}

/* ---------------- 初始化 ---------------- */
onMounted(async function () {
  loadHistory();
  try {
    scenes.value = await api.getScenes();
  } catch (error) {
    ElMessage.error("加载场景失败：" + error.message);
  }
  tracker = new HandTracker();
  detector = new MotionDetector({ tStart: tStart.value, tStop: tStop.value });
  detector.onStateChange = function (state) {
    detectorState.value = state;
    if (state === "SIGNING") {
      pendingSentenceEnd.value = false;
    }
  };
  detector.onActionEnd = function (frames) {
    if (!autoMode.value) return;
    recognizeFrames(frames);
  };
  detector.onSentenceEnd = function () {
    if (!autoMode.value) return;
    if (pendingRecognitions.value > 0) {
      pendingSentenceEnd.value = true;
      return;
    }
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
  if (tracker) tracker.close();
  if (recorder) recorder.close();
  if (audioEl) audioEl.pause();
});

/* ---------------- 主循环 ---------------- */
function loop(now) {
  rafId = requestAnimationFrame(loop);
  const video = videoRef.value;
  if (!cameraOn.value || !video || !detector) return;

  const handMeta = tracker && trackerStatus.value === "ready" ? tracker.detect(video, now) : null;
  handDetected.value = Boolean(handMeta && handMeta.hasHands);

  if (roiEnabled.value && handMeta && handMeta.handBox) {
    liveRoi.value = computeSegmentRoi(
      [{ handBox: handMeta.handBox }],
      video.videoWidth || 1280,
      video.videoHeight || 720,
    );
  } else {
    liveRoi.value = null;
  }

  if (tracker && overlayRef.value) {
    tracker.drawOverlay(
      overlayRef.value,
      video,
      handMeta ? handMeta.landmarks : [],
      roiEnabled.value ? liveRoi.value : null,
    );
  }

  detector.push(video, now, handMeta);
  diff.value = formatDiff(detector.diff);
  frameCount += 1;
  if (!fpsAt) fpsAt = now;
  if (now - fpsAt >= 500) {
    fps.value = String(Math.round((frameCount * 1000) / (now - fpsAt)));
    frameCount = 0;
    fpsAt = now;
  }
}

/* ---------------- 摄像头与手部追踪 ---------------- */
async function initHandTracker() {
  if (!tracker) return;
  if (tracker.status === "ready" || tracker.status === "fallback") {
    trackerStatus.value = tracker.status;
    syncRequireHands();
    return;
  }
  trackerStatus.value = "loading";
  await tracker.init();
  trackerStatus.value = tracker.status;
  syncRequireHands();
}

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
    initHandTracker();
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
  if (overlayRef.value) {
    const ctx = overlayRef.value.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, overlayRef.value.width, overlayRef.value.height);
  }
  cameraOn.value = false;
  handDetected.value = false;
  liveRoi.value = null;
  if (detector) detector.reset();
}

/* ---------------- 识别 ---------------- */
async function recognizeFrames(frames) {
  if (!frames || frames.length < 2) return;
  const grid = composeGridWithMeta(frames, {
    frameWidth: 320,
    frameHeight: 240,
    useRoi: roiEnabled.value,
  });
  lastGridUrl.value = grid.dataUrl;
  lastRoiSource.value = grid.roi ? grid.roi.source : "";
  await recognizeImage(grid.dataUrl);
}

async function recognizeImage(image) {
  pendingRecognitions.value += 1;
  try {
    const result = await api.recognize(image, sceneId.value);
    mockTip.value = result.mock ? "当前为 Mock 识别（未配置 MaaS 密钥）" : "";
    const recognized = Array.isArray(result.words) ? result.words : [];
    const options = [];
    recognized.forEach(function (w) {
      if (w && options.indexOf(w) === -1) options.push(w);
    });
    const alternates = Array.isArray(result.alternatives) ? result.alternatives : [];
    alternates.forEach(function (group) {
      if (Array.isArray(group)) {
        group.forEach(function (word) {
          if (word && options.indexOf(word) === -1) options.push(word);
        });
      } else if (typeof group === "string" && group && options.indexOf(group) === -1) {
        options.push(group);
      }
    });

    if (!recognized.length) {
      if (options.length) {
        candidates.value = options;
        ElMessage.warning("未匹配词表，请点选候选词或下方词表");
      } else {
        ElMessage.warning("这个动作没有识别出词，请重打或从下方词表点选");
      }
      return;
    }

    const confidence = Number(result.confidence || 0);
    if (confidence >= 0.6) {
      recognized.forEach(function (word) {
        addWord(word);
      });
    } else {
      candidates.value = options;
      ElMessage.info("置信度较低（" + Math.round(confidence * 100) + "%），请点选候选词");
    }
  } catch (error) {
    ElMessage.error("识别失败：" + error.message);
  } finally {
    pendingRecognitions.value = Math.max(0, pendingRecognitions.value - 1);
    if (pendingRecognitions.value === 0 && pendingSentenceEnd.value) {
      pendingSentenceEnd.value = false;
      if (words.value.length > 0 && !composing.value) {
        composeNow();
      }
    }
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
  isEmergency.value = false;
  needConfirm.value = false;
  guardTriggered.value = false;
  guardReason.value = "";
}

function clearHistory() {
  history.value = [];
  try {
    localStorage.removeItem(HISTORY_KEY);
  } catch (error) {
    /* ignore */
  }
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function exportHistory() {
  if (!history.value.length) return;
  const lines = history.value.map(function (item) {
    const role = item.role === "other" ? "对方（语音）" : "我（手语）";
    let line = "[" + (item.time || "--:--:--") + "] " + role + "：" + item.text;
    if (item.words && item.words.length) line += "（手语词：" + item.words.join(" · ") + "）";
    if (item.emergency) line += "【紧急求助】";
    return line;
  });
  const now = new Date();
  const header =
    "手语通 SignBridge 对话记录\n" +
    "导出时间：" + now.toLocaleString() + "\n" +
    "场景：" + sceneName.value + "\n" +
    "条数：" + lines.length + "\n\n";
  const blob = new Blob([header + lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download =
    "手语通对话记录-" +
    now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate()) +
    "-" + pad2(now.getHours()) + pad2(now.getMinutes()) + ".txt";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
  ElMessage.success("已导出 " + lines.length + " 条对话记录");
}

function onSceneChange() {
  const scene = scenes.value.find(function (item) {
    return item.id === sceneId.value;
  });
  sceneName.value = scene ? scene.name : sceneId.value;
  clearWords();
}

/* ---------------- 流式造句与紧急求助 ---------------- */
async function composeNow(overrideWords) {
  const isOverride = Array.isArray(overrideWords);
  const wordTexts = isOverride
    ? overrideWords.filter(Boolean)
    : words.value.map(function (item) {
        return item.text;
      });
  if (!wordTexts.length || composing.value) return;

  composing.value = true;
  pendingSentenceEnd.value = false;
  sentence.value = "";
  alternative.value = "";
  isEmergency.value = false;
  needConfirm.value = false;
  guardTriggered.value = false;
  guardReason.value = "";

  const recentHistory = history.value.slice(-5).map(function (turn) {
    return { role: turn.role, text: turn.text };
  });

  await api.composeStream(wordTexts, sceneId.value, recentHistory, {
    onDelta: function (text) {
      sentence.value += text;
    },
    onDone: function (fullSentence, alt, meta) {
      composing.value = false;
      if (fullSentence) sentence.value = fullSentence;
      alternative.value = alt || "";
      isEmergency.value = Boolean(meta && meta.emergency);
      needConfirm.value = Boolean(meta && meta.need_confirm);
      guardTriggered.value = Boolean(meta && meta.guard_triggered);
      guardReason.value = (meta && meta.guard_reason) || "";

      history.value.push({
        role: "me",
        text: sentence.value,
        words: wordTexts.slice(),
        time: formatNowTime(),
        emergency: isEmergency.value,
        needConfirm: needConfirm.value,
        guardTriggered: guardTriggered.value,
      });

      if (!isOverride) {
        words.value = [];
        candidates.value = [];
      }
      speak(sentence.value);
    },
    onError: function (error) {
      composing.value = false;
      ElMessage.error("造句失败：" + error.message);
    },
  });
}

function triggerEmergency() {
  composeNow(["急救"]);
}

function useAlternative(altText) {
  if (!altText) return;
  sentence.value = altText;
  speak(altText);
}

/* ---------------- 语音播放 ---------------- */
async function speak(text) {
  if (!text) return;
  const seq = ++speakSeq;
  if (audioEl) {
    audioEl.pause();
    audioEl = null;
  }
  speaking.value = true;
  try {
    const result = await api.tts(text);
    if (seq !== speakSeq) return;
    audioEl = new Audio("data:audio/wav;base64," + result.audio);
    audioEl.onended = function () {
      if (seq === speakSeq) speaking.value = false;
    };
    audioEl.onerror = function () {
      if (seq === speakSeq) speaking.value = false;
    };
    await audioEl.play();
  } catch (error) {
    if (seq === speakSeq) {
      speaking.value = false;
      ElMessage.error("语音播放失败：" + error.message);
    }
  }
}

/* ---------------- 手动录制与模式开关 ---------------- */
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
    if (video && video.readyState >= 2) {
      const handBox = trackerStatus.value === "ready" && tracker && tracker.lastResult
        ? tracker.lastResult.handBox
        : null;
      manualFrames.push(captureFrame(video, handBox));
    }
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
  pendingSentenceEnd.value = false;
  syncRequireHands();
  if (value && manualRecording.value) stopManualRecord();
}

function onRoiChange() {
  syncRequireHands();
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
    history.value.push({
      role: "other",
      text: otherText.value,
      time: formatNowTime(),
    });
    const quick = await api.quickReply(otherText.value, sceneId.value);
    replies.value = quick.replies || [];
  } catch (error) {
    ElMessage.error("语音识别失败：" + error.message);
  }
}

async function sendQuickReply(text) {
  sentence.value = text;
  history.value.push({
    role: "me",
    text: text,
    time: formatNowTime(),
  });
  await speak(text);
}
</script>

<style scoped>
.translate {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(380px, 1fr);
  gap: 16px;
  align-items: start;
}

.left-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
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

.video,
.overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: scaleX(-1);
}

.overlay {
  pointer-events: none;
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
  line-height: 1.75;
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

.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
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

.grid-preview {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #26304b;
}

.grid-img {
  margin-top: 6px;
  width: 100%;
  max-width: 420px;
  border-radius: 8px;
  border: 1px solid #26304b;
  display: block;
}

.vocab-box {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #26304b;
}

.vocab-title {
  font-size: 12px;
  color: #8b96b3;
  margin-bottom: 8px;
}

.vocab-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 148px;
  overflow-y: auto;
}

.vocab-btn {
  margin-left: 0 !important;
}

.sentence {
  font-size: 22px;
  font-weight: 600;
  min-height: 34px;
  color: #e8ecf8;
}

.confirm-row {
  margin-top: 6px;
}

.alternative {
  margin-top: 6px;
  font-size: 13px;
  color: #8b96b3;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
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

.history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 240px;
  overflow-y: auto;
}

.history-item {
  padding: 8px 10px;
  border-radius: 8px;
  background: #0e1426;
  border: 1px solid #232d47;
}

.history-item.role-other {
  border-left: 3px solid #ffd166;
}

.history-item.role-me {
  border-left: 3px solid #38e8d0;
}

.history-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #8b96b3;
}

.history-role {
  font-weight: 600;
  color: #e8ecf8;
}

.history-body {
  margin-top: 4px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.history-text {
  font-size: 14px;
  color: #e8ecf8;
}

.history-words {
  margin-top: 2px;
  font-size: 12px;
  color: #6b7699;
}

.hint {
  font-size: 12px;
  color: #5b6584;
}

/* ---------------- 大字模式：面对面共看一台设备 ---------------- */
.translate.big-mode .sentence {
  font-size: 40px;
  line-height: 1.45;
}

.translate.big-mode .history-text {
  font-size: 20px;
}

.translate.big-mode .history-meta,
.translate.big-mode .history-words {
  font-size: 14px;
}

.translate.big-mode .other {
  font-size: 22px;
}

.translate.big-mode .replies :deep(.el-button) {
  font-size: 18px;
  padding: 14px 18px;
  height: auto;
}

.translate.big-mode .words :deep(.el-tag) {
  font-size: 16px;
  height: auto;
  padding: 6px 12px;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 1100px) {
  .translate {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .translate {
    gap: 10px;
  }

  .stage {
    min-height: 200px;
  }

  .hud {
    padding: 4px 8px;
    font-size: 10px;
    line-height: 1.5;
  }

  .sentence {
    font-size: 24px;
  }

  .translate.big-mode .sentence {
    font-size: 32px;
  }

  .history-list {
    max-height: 180px;
  }

  .grid-img {
    max-width: 100%;
  }
}
</style>
