<template>
  <el-card class="mb-4">
    <!-- Header -->
    <template #header>
      <el-space class="w-full justify-between" alignment="center">
        <strong>{{ model.name }}</strong>
        <el-tag :type="tagType(model.status)">{{ model.status }}</el-tag>
      </el-space>
    </template>

    <!-- Descriptions -->
    <el-descriptions :column="1" border size="small">
      <el-descriptions-item v-if="model.port" label="Port">{{ model.port }}</el-descriptions-item>
      <el-descriptions-item v-if="model.cuda_device !== undefined" label="GPU">{{ model.cuda_device }}</el-descriptions-item>
      <el-descriptions-item v-if="model.max_model_len" label="Max Tokens">{{ model.max_model_len }}</el-descriptions-item>
      <el-descriptions-item v-if="model.max_length" label="Max Length">{{ model.max_length }}</el-descriptions-item>
      <el-descriptions-item v-if="model.tool_parser" label="Tool Parser">{{ model.tool_parser }}</el-descriptions-item>
    </el-descriptions>

    <!-- 插槽內容 -->
    <div class="my-2">
      <slot />
    </div>

    <!-- 詳細插槽 -->
    <div>
      <slot name="details" />
    </div>

    <!-- 操作按鈕 -->
    <el-divider class="my-4" content-position="left">操作</el-divider>
    <div class="text-right">
      <el-button
        type="success"
        :disabled="model.status === '啟動中' || model.status === '已啟動'"
        @click="openPasswordDialog('start')"
      >
        ▶ 啟動
      </el-button>
      <el-button
        type="danger"
        :disabled="model.status === '未啟動'"
        @click="openPasswordDialog('stop')"
      >
        ⏹ 關閉
      </el-button>
    </div>

    <!-- 密碼對話框 -->
    <el-dialog v-model="dialogVisible" title="請輸入密碼" width="300px" @close="resetDialog">
      <el-input
        v-model="passwordInput"
        type="password"
        show-password
        placeholder="輸入操作密碼"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmOperation">確認</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>


<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'


const correctPassword = import.meta.env.VITE_MODEL_CONTROL_PASSWORD

const props = defineProps({
  model: {
    type: Object,
    required: true
  }
})
const emit = defineEmits(['start', 'stop'])

function tagType(status) {
  switch (status) {
    case '已啟動': return 'success'
    case '啟動中': return 'warning'
    case '未啟動': return 'info'
    case '錯誤': return 'danger'
    default: return 'default'
  }
}

const dialogVisible = ref(false)
const passwordInput = ref('')
const pendingAction = ref<'start' | 'stop' | null>(null)

function openPasswordDialog(action: 'start' | 'stop') {
  pendingAction.value = action
  dialogVisible.value = true
}


function resetDialog() {
  passwordInput.value = ''
  pendingAction.value = null
}

function confirmOperation() {
  if (passwordInput.value === correctPassword) {
    if (pendingAction.value === 'start') {
      emit('start', props.model.name)
    } else if (pendingAction.value === 'stop') {
      emit('stop', props.model.name)
    }
    dialogVisible.value = false
    resetDialog()
  } else {
    ElMessage.error('密碼錯誤')
  }
}
</script>


<style scoped>
.card-details {
  margin-top: 0.75rem;
  font-size: 0.85rem;
  color: #444;
  background: #f9f9f9;
  padding: 0.5rem;
  border-radius: 6px;
}
.card {
  width: 300px;
  border-radius: 10px;
  padding: 1rem;
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border: 1px solid #eee;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.card-header {
  font-size: 1.1rem;
  font-weight: bold;
  margin-bottom: 0.5rem;
}

.card-body {
  font-size: 0.9rem;
  line-height: 1.5;
}

.card-footer {
  margin-top: 1rem;
}

.status {
  font-weight: bold;
  margin-bottom: 0.75rem;
  padding: 0.2rem 0.5rem;
  border-radius: 6px;
  display: inline-block;
  width: fit-content;
}
.status-success {
  background-color: #e0f7e9;
  color: #2e7d32;
}
.status-fail {
  background-color: #fdecea;
  color: #c62828;
}
.status-pending {
  background-color: #fff3cd;
  color: #8a6d3b;
}
.status-idle {
  background-color: #f4f4f4;
  color: #666;
}

.button-row {
  display: flex;
  gap: 12px;
}

/* 共用按鈕樣式 */
button {
  padding: 6px 12px;
  border: none;
  border-radius: 4px;
  color: white;
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.primary {
  background-color: #007bff;
}
.primary:disabled {
  background-color: #007bff;
}

.danger {
  background-color: #e74c3c;
}
.danger:disabled {
  background-color: #f5b7b1;
}
</style>
