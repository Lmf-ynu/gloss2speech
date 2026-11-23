<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand">
        <span class="logo">手语通</span>
        <span class="sub">SignBridge · 实时手语翻译</span>
      </div>
      <div class="header-right">
        <el-tag :type="serviceTagType" effect="dark" size="small" class="service-tag" @click="promptForToken">{{ serviceText }}</el-tag>
      </div>
    </header>
    <main class="app-main">
      <Translate />
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import Translate from "./views/Translate.vue";
import { api } from "./api";

const serviceText = ref("检查后端中…");
const serviceTagType = ref("info");

async function checkHealth() {
  try {
    const health = await api.health();
    const services = health.services || {};
    serviceText.value = "后端 " + (services.vision === "mock" ? "Mock" : "MaaS") + " · " + (services.sis === "mock" ? "Mock" : "SIS");
    serviceTagType.value = services.vision === "mock" || services.sis === "mock" ? "warning" : "success";
    return true;
  } catch (error) {
    serviceText.value = "后端未连接（" + (error.message || error) + "）";
    serviceTagType.value = "danger";
    return false;
  }
}

async function promptForToken() {
  try {
    const result = await ElMessageBox.prompt(
      "后端已开启访问口令（API_TOKEN），请输入后重试：",
      "需要访问口令",
      { confirmButtonText: "确定", cancelButtonText: "取消", inputType: "password" },
    );
    const token = (result.value || "").trim();
    if (!token) return;
    api.setToken(token);
    ElMessage.success("口令已保存到本机，正在重新连接…");
    window.location.reload();
  } catch (error) {
    /* 用户取消 */
  }
}

onMounted(async () => {
  try {
    await api.getScenes();
  } catch (error) {
    if (error && error.status === 401) {
      serviceText.value = "需要访问口令（点此输入）";
      serviceTagType.value = "danger";
      await promptForToken();
      return;
    }
  }
  await checkHealth();
});
</script>
