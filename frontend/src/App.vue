<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand">
        <span class="logo">手语通</span>
        <span class="sub">SignBridge · 实时手语翻译</span>
      </div>
      <div class="header-right">
        <el-tag :type="serviceTagType" effect="dark" size="small">{{ serviceText }}</el-tag>
      </div>
    </header>
    <main class="app-main">
      <Translate />
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import Translate from "./views/Translate.vue";
import { api } from "./api";

const serviceText = ref("检查后端中…");
const serviceTagType = ref("info");

onMounted(async () => {
  try {
    const health = await api.health();
    const services = health.services || {};
    serviceText.value = "后端 " + (services.vision === "mock" ? "Mock" : "MaaS") + " · " + (services.sis === "mock" ? "Mock" : "SIS");
    serviceTagType.value = services.vision === "mock" || services.sis === "mock" ? "warning" : "success";
  } catch (error) {
    serviceText.value = "后端未连接（" + (error.message || error) + "）";
    serviceTagType.value = "danger";
  }
});
</script>
