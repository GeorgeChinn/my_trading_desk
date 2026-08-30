<template>
  <span class="badge" :class="status">{{ status }}</span>
</template>
<script setup>
defineProps({ status: { type: String, required: true } });
</script>
