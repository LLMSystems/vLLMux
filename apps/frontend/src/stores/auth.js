import { ref } from 'vue'
import { defineStore } from 'pinia'

export const useAuthStore = defineStore('auth', () => {
  const isLoggedIn = ref(false)
  const username = ref('')

  function login(name) {
    isLoggedIn.value = true
    username.value = name
  }

  function logout() {
    isLoggedIn.value = false
    username.value = ''
  }

  return { isLoggedIn, username, login, logout }
})
