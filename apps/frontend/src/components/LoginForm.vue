<template>
  <div class="login-container">
    <el-card class="login-card" shadow="always">
      <template #header>
        <div class="login-title">LLM Router 管理者登入</div>
      </template>

      <el-form :model="form" @submit.prevent="handleLogin" label-position="top">
        <el-form-item label="使用者帳號">
          <el-input v-model="form.username" placeholder="請輸入帳號" />
        </el-form-item>
        <el-form-item label="密碼">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="請輸入密碼"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" class="w-full" @click="handleLogin">登入</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const emit = defineEmits(['login'])

const form = ref({
  username: '',
  password: ''
})

const defaultAdmin = {
  username: 'admin',
  password: 'admin123'
}

function handleLogin() {
  if (!form.value.username || !form.value.password) {
    ElMessage.error('請輸入帳號與密碼')
    return
  }

  if (
    form.value.username === defaultAdmin.username &&
    form.value.password === defaultAdmin.password
  ) {
    ElMessage.success('登入成功')
    emit('login', form.value.username)
  } else {
    ElMessage.error('帳號或密碼錯誤')
  }
}
</script>


<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background: #f5f7fa;
}

.login-card {
  width: 100%;
  max-width: 400px;
  border-radius: 12px;
  padding: 20px;
}

.login-title {
  font-size: 20px;
  font-weight: bold;
  text-align: center;
}
</style>
