import { ref } from "vue";

export const pageLoading = ref(false);
export const pageLoadingText = ref("加载中…");

let inflight = 0;

export function beginLoading(text) {
  inflight += 1;
  if (text) pageLoadingText.value = text;
  else if (inflight === 1) pageLoadingText.value = "加载中…";
  pageLoading.value = true;
}

export function endLoading() {
  inflight = Math.max(0, inflight - 1);
  if (inflight === 0) pageLoading.value = false;
}

export function setLoadingText(text) {
  if (text) pageLoadingText.value = text;
}

export function showLoading(text) {
  pageLoading.value = true;
  if (text) pageLoadingText.value = text;
}
