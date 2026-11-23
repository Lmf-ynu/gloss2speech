/**
 * 与后端 6 个接口通信（对应《工程实现方案》§4）。
 * 开发时走 vite 代理；生产时默认走同源 /api（Nginx 反代），可用 VITE_API_BASE 覆盖。
 */
const BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

async function request(path, options) {
  const response = await fetch(BASE + path, options);
  if (!response.ok) {
    let detail = response.status + " " + response.statusText;
    try {
      const data = await response.json();
      if (data && data.detail) detail = data.detail;
    } catch (error) {
      /* ignore */
    }
    throw new Error(detail);
  }
  return response.json();
}

function postJSON(path, body) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  base: BASE,
  health: () => request("/api/health"),
  getScenes: () => request("/api/scenes"),

  recognize(imageBase64, sceneId) {
    return postJSON("/api/recognize", { image: imageBase64, scene_id: sceneId });
  },

  /** 流式造句：SSE，逐段回调 onDelta，结束回调 onDone(sentence, alternative)。 */
  async composeStream(words, sceneId, history, handlers) {
    const onDelta = handlers.onDelta || function () {};
    const onDone = handlers.onDone || function () {};
    const onError = handlers.onError || function () {};
    const response = await fetch(BASE + "/api/compose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ words, scene_id: sceneId, history: history || [] }),
    });
    if (!response.ok || !response.body) {
      onError(new Error("SSE 连接失败：" + response.status));
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index = buffer.indexOf("\n\n");
        while (index >= 0) {
          const block = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          const line = block.trim();
          if (line.startsWith("data:")) {
            const payload = line.slice(5).trim();
            if (payload === "[DONE]") return;
            try {
              const event = JSON.parse(payload);
              if (event.type === "delta") onDelta(event.text || "");
              else if (event.type === "done") onDone(event.sentence || "", event.alternative || "", event);
              else if (event.type === "error") onError(new Error(event.message || "造句失败"));
            } catch (error) {
              /* 忽略解析失败的半包 */
            }
          }
          index = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      onError(error);
    }
  },

  tts(text) {
    return postJSON("/api/tts", { text });
  },

  asr(audioBase64) {
    return postJSON("/api/asr", { audio: audioBase64 });
  },

  quickReply(text, sceneId) {
    return postJSON("/api/quick-reply", { text, scene_id: sceneId });
  },
};
