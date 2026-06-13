<template>
  <LoginForm @login="handleLogin" />
</template>

<script setup>
import { useRouter } from 'vue-router'

import LoginForm from '@/components/LoginForm.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

function handleLogin(username) {
  auth.login(username)
  router.push({ name: 'dashboard' })
}
</script>
