<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Bot, Loader2, Send, Trash2, User } from '@lucide/vue'
import { api } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import Card from '@/components/ui/Card.vue'
import Tabs from '@/components/ui/Tabs.vue'
import TabsList from '@/components/ui/TabsList.vue'
import TabsTrigger from '@/components/ui/TabsTrigger.vue'
import TabsContent from '@/components/ui/TabsContent.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Textarea from '@/components/ui/Textarea.vue'
import Badge from '@/components/ui/Badge.vue'
import { toast } from '@/lib/toast'
import { formatLatency } from '@/lib/utils'

const models = useModelsStore()
const tab = ref('chat')

const modelOptions = ref<string[]>([])
const model = ref('')
const maxTokens = ref(256)
const temperature = ref(0.7)
const stream = ref(true)

onMounted(async () => {
  try {
    const list = await api.routerModels()
    modelOptions.value = list.data.map((m) => m.id)
    model.value = modelOptions.value[0] ?? ''
  } catch {
    // Fall back to configured LLM groups if the router list is unavailable.
    modelOptions.value = [...new Set(models.llms.map((m) => m.key.split('::')[0] ?? m.key))]
    model.value = modelOptions.value[0] ?? ''
  }
})

// ---- Chat ----
interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}
const messages = ref<ChatMsg[]>([])
const chatInput = ref('')
const busy = ref(false)
const lastLatency = ref<number | null>(null)

async function sendChat() {
  if (!chatInput.value.trim() || !model.value || busy.value) return
  const userMsg: ChatMsg = { role: 'user', content: chatInput.value.trim() }
  messages.value.push(userMsg)
  chatInput.value = ''
  const assistant: ChatMsg = { role: 'assistant', content: '' }
  messages.value.push(assistant)
  busy.value = true
  const t0 = performance.now()

  try {
    const res = await api.routerFetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model.value,
        messages: messages.value.slice(0, -1).map((m) => ({ role: m.role, content: m.content })),
        max_tokens: maxTokens.value,
        temperature: temperature.value,
        stream: stream.value,
      }),
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)

    if (stream.value && res.body) {
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data:')) continue
          const payload = trimmed.slice(5).trim()
          if (payload === '[DONE]') continue
          try {
            const json = JSON.parse(payload)
            const delta = json.choices?.[0]?.delta?.content ?? ''
            assistant.content += delta
          } catch {
            /* ignore partial frames */
          }
        }
      }
    } else {
      const json = await res.json()
      assistant.content = json.choices?.[0]?.message?.content ?? '（空回應）'
    }
  } catch (e) {
    assistant.content = ''
    messages.value.pop() // drop empty assistant bubble
    toast.error('對話請求失敗', { description: String(e) })
  } finally {
    lastLatency.value = performance.now() - t0
    busy.value = false
  }
}
function clearChat() {
  messages.value = []
  lastLatency.value = null
}

// ---- Embedding / Rerank ----
const embModel = ref('')
const embInput = ref('The quick brown fox\nA lazy dog sleeps')
const rerankQuery = ref('')
const embBusy = ref(false)
const embResult = ref<{ index: number; value: number; preview: string }[] | null>(null)
const embDim = ref<number | null>(null)

async function runEmbedding() {
  if (embBusy.value) return
  embBusy.value = true
  embResult.value = null
  embDim.value = null
  const inputs = embInput.value.split('\n').filter((s) => s.trim())
  try {
    const body: Record<string, unknown> = { model: embModel.value || 'm3e-base', input: inputs }
    if (rerankQuery.value.trim()) body.query = rerankQuery.value.trim()
    const res = await api.routerFetch('/v1/embeddings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    const json = await res.json()
    const data = json.data ?? []
    embResult.value = data.map((d: { embedding: number[] | number }, i: number) => {
      const isRerank = typeof d.embedding === 'number'
      if (!isRerank && Array.isArray(d.embedding)) embDim.value = d.embedding.length
      return {
        index: i,
        value: isRerank ? (d.embedding as number) : (d.embedding as number[]).length,
        preview: inputs[i] ?? '',
      }
    })
    if (rerankQuery.value.trim() && embResult.value) {
      embResult.value.sort((a, b) => b.value - a.value)
    }
  } catch (e) {
    toast.error('嵌入請求失敗', { description: String(e) })
  } finally {
    embBusy.value = false
  }
}

const isRerankMode = computed(() => !!rerankQuery.value.trim())
</script>

<template>
  <div class="p-6">
    <Tabs v-model="tab" class="space-y-4">
      <TabsList>
        <TabsTrigger value="chat"><Bot class="size-4" />對話</TabsTrigger>
        <TabsTrigger value="embedding">嵌入 / 重排序</TabsTrigger>
      </TabsList>

      <!-- Chat -->
      <TabsContent value="chat">
        <div class="grid gap-4 lg:grid-cols-[1fr_18rem]">
          <Card glass class="flex h-[calc(100vh-12rem)] flex-col">
            <div class="flex-1 space-y-4 overflow-y-auto p-5">
              <div
                v-for="(m, i) in messages"
                :key="i"
                class="flex gap-3"
                :class="m.role === 'user' ? 'flex-row-reverse' : ''"
              >
                <div
                  class="flex size-7 shrink-0 items-center justify-center rounded-full"
                  :class="m.role === 'user' ? 'bg-[var(--chart-1)] text-white' : 'bg-muted'"
                >
                  <User v-if="m.role === 'user'" class="size-4" />
                  <Bot v-else class="size-4" />
                </div>
                <div
                  class="max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed"
                  :class="
                    m.role === 'user'
                      ? 'bg-[var(--chart-1)] text-white'
                      : 'border border-border/60 bg-background/40'
                  "
                >
                  {{ m.content }}<span v-if="busy && i === messages.length - 1" class="animate-pulse">▋</span>
                </div>
              </div>
              <div
                v-if="!messages.length"
                class="flex h-full flex-col items-center justify-center text-center text-muted-foreground"
              >
                <Bot class="size-10 opacity-30" />
                <p class="mt-3 text-sm">發送訊息以透過路由器測試 <span class="font-mono">{{ model || '模型' }}</span>。</p>
              </div>
            </div>
            <div class="border-t border-border/70 p-4">
              <div class="flex items-end gap-2">
                <Textarea
                  v-model="chatInput"
                  placeholder="輸入訊息…（Enter 送出，Shift+Enter 換行）"
                  class="min-h-[44px] resize-none"
                  @keydown.enter.exact.prevent="sendChat"
                />
                <Button :disabled="busy" class="h-11" @click="sendChat">
                  <Loader2 v-if="busy" class="size-4 animate-spin" /><Send v-else class="size-4" />
                </Button>
              </div>
              <div class="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                <span v-if="lastLatency" class="tabular">⏱ {{ formatLatency(lastLatency) }}</span>
                <button v-if="messages.length" class="ml-auto flex items-center gap-1 hover:text-foreground" @click="clearChat">
                  <Trash2 class="size-3.5" />清除
                </button>
              </div>
            </div>
          </Card>

          <!-- Params -->
          <Card class="h-fit p-5">
            <p class="mb-4 text-sm font-semibold">參數</p>
            <div class="space-y-4 text-sm">
              <label class="block">
                <span class="text-xs text-muted-foreground">模型</span>
                <select
                  v-model="model"
                  class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
                >
                  <option v-for="o in modelOptions" :key="o" :value="o">{{ o }}</option>
                </select>
              </label>
              <label class="block">
                <span class="flex justify-between text-xs text-muted-foreground"
                  >最大 Tokens <span class="tabular text-foreground">{{ maxTokens }}</span></span
                >
                <input v-model.number="maxTokens" type="range" min="16" max="2048" step="16" class="mt-1 w-full accent-[var(--chart-1)]" />
              </label>
              <label class="block">
                <span class="flex justify-between text-xs text-muted-foreground"
                  >溫度 <span class="tabular text-foreground">{{ temperature.toFixed(1) }}</span></span
                >
                <input v-model.number="temperature" type="range" min="0" max="2" step="0.1" class="mt-1 w-full accent-[var(--chart-1)]" />
              </label>
              <label class="flex items-center justify-between">
                <span class="text-xs text-muted-foreground">串流回應</span>
                <input v-model="stream" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
              </label>
            </div>
          </Card>
        </div>
      </TabsContent>

      <!-- Embedding / Rerank -->
      <TabsContent value="embedding">
        <div class="grid gap-4 lg:grid-cols-2">
          <Card class="p-5">
            <p class="mb-4 text-sm font-semibold">請求</p>
            <div class="space-y-4 text-sm">
              <label class="block">
                <span class="text-xs text-muted-foreground">模型</span>
                <Input v-model="embModel" placeholder="m3e-base / bge-reranker-large" class="mt-1" />
              </label>
              <label class="block">
                <span class="text-xs text-muted-foreground">查詢（設定後進入重排序模式）</span>
                <Input v-model="rerankQuery" placeholder="留空則為純嵌入模式" class="mt-1" />
              </label>
              <label class="block">
                <span class="text-xs text-muted-foreground">輸入（每行一筆）</span>
                <Textarea v-model="embInput" class="mt-1 min-h-[140px] font-mono text-xs" />
              </label>
              <Button :disabled="embBusy" @click="runEmbedding">
                <Loader2 v-if="embBusy" class="size-4 animate-spin" />
                {{ isRerankMode ? '重排序' : '嵌入' }}
              </Button>
            </div>
          </Card>

          <Card class="p-5">
            <div class="mb-4 flex items-center gap-2">
              <p class="text-sm font-semibold">結果</p>
              <Badge v-if="isRerankMode" variant="default">rerank</Badge>
              <Badge v-else-if="embDim" variant="muted">dim {{ embDim }}</Badge>
            </div>
            <div v-if="embResult" class="space-y-2">
              <div
                v-for="r in embResult"
                :key="r.index"
                class="rounded-lg border border-border/60 bg-background/40 p-3"
              >
                <div class="flex items-center justify-between gap-3">
                  <span class="truncate text-sm">{{ r.preview }}</span>
                  <span class="shrink-0 tabular text-sm font-medium">
                    {{ isRerankMode ? r.value.toFixed(4) : `${r.value} 維向量` }}
                  </span>
                </div>
                <div v-if="isRerankMode" class="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    class="h-full rounded-full bg-[var(--chart-2)]"
                    :style="{ width: `${Math.max(2, Math.min(100, r.value * 100))}%` }"
                  />
                </div>
              </div>
            </div>
            <p v-else class="py-10 text-center text-sm text-muted-foreground">
              執行請求以查看{{ isRerankMode ? '相關性分數' : '向量維度' }}。
            </p>
          </Card>
        </div>
      </TabsContent>
    </Tabs>
  </div>
</template>
