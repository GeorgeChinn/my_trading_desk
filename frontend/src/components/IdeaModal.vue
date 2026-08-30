<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <h2>记录我的想法</h2>
      <p class="sub">只存本地。这是事实记录的旁注，不是成交指令。</p>
      <div class="grid cols-2" style="margin-bottom:12px">
        <label class="field">
          <span>代码（可选）</span>
          <input v-model="code" placeholder="600519" />
        </label>
      </div>
      <label class="field">
        <span>想法</span>
        <textarea v-model="text" placeholder="今天看到什么、为什么还不动手…"></textarea>
      </label>
      <p v-if="err" class="sub" style="color:var(--red)">{{ err }}</p>
      <div class="row-btns" style="margin-top:14px;justify-content:flex-end">
        <button class="btn ghost" @click="$emit('close')">取消</button>
        <button class="btn primary" @click="save">保存到本地</button>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref } from "vue";
import { api } from "../api";
const emit = defineEmits(["close"]);
const text = ref("");
const code = ref("");
const err = ref("");
async function save() {
  err.value = "";
  try {
    await api.addIdea({ text: text.value, code: code.value });
    emit("close");
  } catch (e) {
    err.value = e.message;
  }
}
</script>
