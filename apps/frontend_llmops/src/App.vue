<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { Toaster } from 'vue-sonner'
import 'vue-sonner/style.css'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import StatusBar from '@/components/layout/StatusBar.vue'
import ModelControlDialog from '@/components/ModelControlDialog.vue'
import { useModelsStore } from '@/stores/models'
import { useResourcesStore } from '@/stores/resources'
import { useTrafficStore } from '@/stores/traffic'
import { useTheme } from '@/composables/useTheme'

// Initialise theme (reads persisted preference / system default).
const { isDark } = useTheme()

const models = useModelsStore()
const resources = useResourcesStore()
const traffic = useTrafficStore()

onMounted(() => {
  models.connect()
  resources.start()
  traffic.start()
})
onBeforeUnmount(() => {
  models.disconnect()
  resources.stop()
  traffic.stop()
})
</script>

<template>
  <div class="flex h-screen overflow-hidden bg-background">
    <AppSidebar />
    <div class="flex min-w-0 flex-1 flex-col">
      <StatusBar />
      <main class="flex-1 overflow-y-auto">
        <RouterView v-slot="{ Component }">
          <transition
            enter-active-class="transition-opacity duration-200"
            enter-from-class="opacity-0"
            leave-active-class="transition-opacity duration-150"
            leave-to-class="opacity-0"
            mode="out-in"
          >
            <component :is="Component" />
          </transition>
        </RouterView>
      </main>
    </div>
    <ModelControlDialog />
    <Toaster position="bottom-right" :theme="isDark ? 'dark' : 'light'" rich-colors />
  </div>
</template>
