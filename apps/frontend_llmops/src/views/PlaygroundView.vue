<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Bot, Loader2, Send, Square, Trash2, User, X } from '@lucide/vue'
import { api } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { useModelOptions } from '@/composables/useModelOptions'
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

// Only ready models are routable — each ready group plus the LoRA adapters
// mounted on it, kept reactive so options appear/disappear as models start/stop.
const { options: modelOptions } = useModelOptions()
const model = ref('')
const maxTokens = ref(256)
const temperature = ref(0.7)
const stream = ref(true)

// Keep the single-chat selection valid as the ready set changes.
watch(
  modelOptions,
  (opts) => {
    if (!opts.some((o) => o.value === model.value)) model.value = opts[0]?.value ?? ''
  },
  { immediate: true },
)

// ---- Chat ----
interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  reasoning?: string // thinking, when the model uses a reasoning parser (reasoning_content)
}
interface Usage {
  completion_tokens?: number
  total_tokens?: number
}
const messages = ref<ChatMsg[]>([])
const chatInput = ref('')
const systemPrompt = ref('')
const busy = ref(false)

// Auto-scroll a message pane to the bottom as content streams in — but only when
// the user is already at the bottom, so scrolling up to read history isn't yanked
// back down. `_stick` is captured *before* the DOM grows (in beforeUpdate).
type StickyEl = HTMLElement & { _stick?: boolean }
const vAutoscroll = {
  mounted: (el: StickyEl) => {
    el.scrollTop = el.scrollHeight
  },
  beforeUpdate: (el: StickyEl) => {
    el._stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  },
  updated: (el: StickyEl) => {
    if (el._stick) el.scrollTop = el.scrollHeight
  },
}
const lastLatency = ref<number | null>(null)
const lastUsage = ref<Usage | null>(null)
// One controller per send (single or compare) so the Stop button aborts all.
let abortController: AbortController | null = null

// ---- Compare mode (one prompt → many models) ----
interface CompareLane {
  model: string
  messages: ChatMsg[]
  busy: boolean
  latency: number | null
  ttft: number | null // time-to-first-token
  tps: number | null // generated tokens/sec
  tokens: number | null
}
const compareMode = ref(false)
const lanes = ref<CompareLane[]>([])

const anyBusy = computed(() =>
  compareMode.value ? lanes.value.some((l) => l.busy) : busy.value,
)

function toggleLane(m: string) {
  const i = lanes.value.findIndex((l) => l.model === m)
  if (i >= 0) lanes.value.splice(i, 1)
  else lanes.value.push({ model: m, messages: [], busy: false, latency: null, ttft: null, tps: null, tokens: null })
}
function isSelected(m: string) {
  return lanes.value.some((l) => l.model === m)
}

function tokensPerSec(usage: Usage | null, latencyMs: number): number | null {
  if (!usage?.completion_tokens || latencyMs <= 0) return null
  return usage.completion_tokens / (latencyMs / 1000)
}

/** Shared streaming core used by both single chat and each compare lane.
 *  `history` must already include the latest user message but NOT the empty
 *  assistant bubble that `assistant` points at. Returns the OpenAI usage block
 *  (token counts) when the backend reports it. */
async function streamChatCompletion(
  modelId: string,
  history: ChatMsg[],
  assistant: ChatMsg,
  opts: { onFirstToken?: () => void; signal?: AbortSignal } = {},
): Promise<Usage | null> {
  const msgs: { role: string; content: string }[] = []
  if (systemPrompt.value.trim()) msgs.push({ role: 'system', content: systemPrompt.value.trim() })
  for (const m of history) msgs.push({ role: m.role, content: m.content })

  const res = await api.routerFetch('/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: opts.signal,
    body: JSON.stringify({
      model: modelId,
      messages: msgs,
      max_tokens: maxTokens.value,
      temperature: temperature.value,
      stream: stream.value,
    }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)

  let usage: Usage | null = null
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
          if (json.usage) usage = json.usage // final chunk carries token counts
          const d = json.choices?.[0]?.delta ?? {}
          // Reasoning parser splits <think> into a separate field; vLLM uses
          // `reasoning`, some OpenAI-compatible servers use `reasoning_content`.
          const think = d.reasoning ?? d.reasoning_content ?? ''
          if (think) {
            opts.onFirstToken?.()
            assistant.reasoning = (assistant.reasoning ?? '') + think
          }
          const delta = d.content ?? ''
          if (delta) {
            opts.onFirstToken?.()
            assistant.content += delta
          }
        } catch {
          /* ignore partial frames */
        }
      }
    }
  } else {
    const json = await res.json()
    opts.onFirstToken?.()
    const msg = json.choices?.[0]?.message ?? {}
    const think = msg.reasoning ?? msg.reasoning_content
    if (think) assistant.reasoning = think
    // content can be null when the model spent its whole budget thinking.
    assistant.content = msg.content ?? (think ? '' : '（空回應）')
    usage = json.usage ?? null
  }
  return usage
}

function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError'
}

async function sendChat() {
  if (!chatInput.value.trim() || !model.value || busy.value) return
  const userMsg: ChatMsg = { role: 'user', content: chatInput.value.trim() }
  messages.value.push(userMsg)
  chatInput.value = ''
  messages.value.push({ role: 'assistant', content: '' })
  // Read the assistant back from the array so we mutate Vue's reactive proxy
  // (not the raw object) — otherwise streamed deltas wouldn't render live.
  const assistant = messages.value[messages.value.length - 1]!
  busy.value = true
  lastUsage.value = null
  const t0 = performance.now()
  try {
    lastUsage.value = await streamChatCompletion(model.value, messages.value.slice(0, -1), assistant, {
      signal: abortController?.signal,
    })
  } catch (e) {
    if (isAbort(e)) {
      if (!assistant.content) messages.value.pop() // nothing streamed yet — drop bubble
    } else {
      messages.value.pop()
      toast.error('對話請求失敗', { description: String(e) })
    }
  } finally {
    lastLatency.value = performance.now() - t0
    busy.value = false
  }
}

/** Stream one lane independently so one model failing never blocks the rest. */
async function runLane(lane: CompareLane) {
  const assistant = lane.messages[lane.messages.length - 1]
  if (!assistant) return
  lane.busy = true
  lane.latency = null
  lane.ttft = null
  lane.tps = null
  lane.tokens = null
  const history = lane.messages.slice(0, -1)
  const t0 = performance.now()
  try {
    const usage = await streamChatCompletion(lane.model, history, assistant, {
      signal: abortController?.signal,
      onFirstToken: () => {
        if (lane.ttft === null) lane.ttft = performance.now() - t0
      },
    })
    lane.tokens = usage?.completion_tokens ?? null
    lane.tps = tokensPerSec(usage, performance.now() - t0)
  } catch (e) {
    if (isAbort(e)) {
      if (!assistant.content) lane.messages.pop()
    } else {
      lane.messages.pop()
      toast.error(`${lane.model} 請求失敗`, { description: String(e) })
    }
  } finally {
    lane.latency = performance.now() - t0
    lane.busy = false
  }
}

async function sendCompare() {
  if (!chatInput.value.trim() || !lanes.value.length || anyBusy.value) return
  const prompt = chatInput.value.trim()
  chatInput.value = ''
  for (const lane of lanes.value) {
    lane.messages.push({ role: 'user', content: prompt })
    lane.messages.push({ role: 'assistant', content: '' })
  }
  // Fan out in parallel; allSettled so one failure doesn't abort the others.
  await Promise.allSettled(lanes.value.map((lane) => runLane(lane)))
}

function send() {
  abortController = new AbortController()
  if (compareMode.value) sendCompare()
  else sendChat()
}

function stop() {
  abortController?.abort()
}

const lastTps = computed(() => tokensPerSec(lastUsage.value, lastLatency.value ?? 0))

function clearChat() {
  messages.value = []
  lastLatency.value = null
}
function clearCompare() {
  for (const lane of lanes.value) {
    lane.messages = []
    lane.latency = null
    lane.ttft = null
  }
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

// Embedding/reranking model picker — only the models a READY embedding server
// actually serves, switching list by mode (rerank when a query is set).
const embeddingReady = computed(() => models.byKey.get('embedding::default')?.state === 'ready')
const embModelOptions = computed(() => {
  const e = models.config?.embedding_server
  if (!e) return []
  return isRerankMode.value ? Object.keys(e.reranking_models) : Object.keys(e.embedding_models)
})
watch(
  embModelOptions,
  (opts) => {
    if (!opts.includes(embModel.value)) embModel.value = opts[0] ?? ''
  },
  { immediate: true },
)
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
          <!-- Single-model conversation -->
          <Card v-if="!compareMode" glass class="flex h-[calc(100vh-12rem)] flex-col">
            <div v-autoscroll class="flex-1 space-y-4 overflow-y-auto p-5">
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
                  <details v-if="m.role === 'assistant' && m.reasoning" open class="mb-2">
                    <summary class="cursor-pointer text-xs text-muted-foreground">💭 思考過程</summary>
                    <div class="mt-1 whitespace-pre-wrap border-l-2 border-border/60 pl-2 text-xs text-muted-foreground">{{ m.reasoning }}</div>
                  </details>
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
                  @keydown.enter.exact.prevent="send"
                />
                <Button v-if="busy" variant="outline" class="h-11" title="停止生成" @click="stop">
                  <Square class="size-4" />
                </Button>
                <Button v-else class="h-11" @click="send">
                  <Send class="size-4" />
                </Button>
              </div>
              <div class="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                <span v-if="lastLatency" class="tabular">⏱ {{ formatLatency(lastLatency) }}</span>
                <span v-if="lastUsage?.completion_tokens" class="tabular">{{ lastUsage.completion_tokens }} tok</span>
                <span v-if="lastTps" class="tabular">{{ lastTps.toFixed(1) }} tok/s</span>
                <button v-if="messages.length" class="ml-auto flex items-center gap-1 hover:text-foreground" @click="clearChat">
                  <Trash2 class="size-3.5" />清除
                </button>
              </div>
            </div>
          </Card>

          <!-- Compare: one prompt, many models side by side -->
          <div v-else class="flex h-[calc(100vh-12rem)] flex-col gap-3">
            <div
              v-if="!lanes.length"
              class="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border/60 text-center text-muted-foreground"
            >
              <Bot class="size-10 opacity-30" />
              <p class="mt-3 text-sm">在右側選擇至少一個模型以開始並排對比。</p>
            </div>
            <div v-else class="flex flex-1 gap-3 overflow-x-auto">
              <Card
                v-for="lane in lanes"
                :key="lane.model"
                glass
                class="flex min-w-[280px] flex-1 flex-col"
              >
                <div class="flex items-center gap-2 border-b border-border/70 px-4 py-2.5">
                  <span class="truncate font-mono text-xs font-semibold">{{ lane.model }}</span>
                  <Loader2 v-if="lane.busy" class="size-3.5 shrink-0 animate-spin text-muted-foreground" />
                  <div class="ml-auto flex shrink-0 items-center gap-1.5">
                    <Badge v-if="lane.ttft != null" variant="muted" class="tabular">首字 {{ formatLatency(lane.ttft) }}</Badge>
                    <Badge v-if="lane.latency != null" variant="muted" class="tabular">⏱ {{ formatLatency(lane.latency) }}</Badge>
                    <Badge v-if="lane.tps != null" variant="muted" class="tabular">{{ lane.tps.toFixed(1) }} tok/s</Badge>
                    <button class="text-muted-foreground hover:text-foreground" title="移除此模型" @click="toggleLane(lane.model)">
                      <X class="size-3.5" />
                    </button>
                  </div>
                </div>
                <div v-autoscroll class="flex-1 space-y-3 overflow-y-auto p-4">
                  <div
                    v-for="(m, i) in lane.messages"
                    :key="i"
                    class="flex gap-2"
                    :class="m.role === 'user' ? 'flex-row-reverse' : ''"
                  >
                    <div
                      class="flex size-6 shrink-0 items-center justify-center rounded-full"
                      :class="m.role === 'user' ? 'bg-[var(--chart-1)] text-white' : 'bg-muted'"
                    >
                      <User v-if="m.role === 'user'" class="size-3.5" />
                      <Bot v-else class="size-3.5" />
                    </div>
                    <div
                      class="max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed"
                      :class="
                        m.role === 'user'
                          ? 'bg-[var(--chart-1)] text-white'
                          : 'border border-border/60 bg-background/40'
                      "
                    >
                      <details v-if="m.role === 'assistant' && m.reasoning" open class="mb-1.5">
                        <summary class="cursor-pointer text-xs text-muted-foreground">💭 思考過程</summary>
                        <div class="mt-1 whitespace-pre-wrap border-l-2 border-border/60 pl-2 text-xs text-muted-foreground">{{ m.reasoning }}</div>
                      </details>
                      {{ m.content }}<span v-if="lane.busy && i === lane.messages.length - 1" class="animate-pulse">▋</span>
                    </div>
                  </div>
                  <div
                    v-if="!lane.messages.length"
                    class="flex h-full items-center justify-center text-center text-xs text-muted-foreground"
                  >
                    等待提問…
                  </div>
                </div>
              </Card>
            </div>

            <!-- Shared input fans out to every lane -->
            <Card class="p-4">
              <div class="flex items-end gap-2">
                <Textarea
                  v-model="chatInput"
                  placeholder="同一個問題會同時送給所有選中的模型…（Enter 送出）"
                  class="min-h-[44px] resize-none"
                  @keydown.enter.exact.prevent="send"
                />
                <Button v-if="anyBusy" variant="outline" class="h-11" title="停止生成" @click="stop">
                  <Square class="size-4" />
                </Button>
                <Button v-else :disabled="!lanes.length" class="h-11" @click="send">
                  <Send class="size-4" />
                </Button>
              </div>
              <div class="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                <span>{{ lanes.length }} 個模型並排</span>
                <button
                  v-if="lanes.some((l) => l.messages.length)"
                  class="ml-auto flex items-center gap-1 hover:text-foreground"
                  @click="clearCompare"
                >
                  <Trash2 class="size-3.5" />清除全部
                </button>
              </div>
            </Card>
          </div>

          <!-- Params -->
          <Card class="h-fit p-5">
            <p class="mb-4 text-sm font-semibold">參數</p>
            <div class="space-y-4 text-sm">
              <label class="flex items-center justify-between">
                <span class="text-xs text-muted-foreground">對比模式（多模型同問題）</span>
                <input v-model="compareMode" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
              </label>

              <label class="block">
                <span class="text-xs text-muted-foreground">系統提示（System prompt）</span>
                <Textarea
                  v-model="systemPrompt"
                  placeholder="留空則不送。例如：你是一位專業的繁體中文助理。"
                  class="mt-1 min-h-[60px] resize-none text-xs"
                />
              </label>

              <!-- Single: one model -->
              <label v-if="!compareMode" class="block">
                <span class="text-xs text-muted-foreground">模型（僅顯示已就緒）</span>
                <select
                  v-if="modelOptions.length"
                  v-model="model"
                  class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
                >
                  <option v-for="o in modelOptions" :key="o.value" :value="o.value">
                    {{ o.isLora ? `${o.label}  (LoRA)` : o.label }}
                  </option>
                </select>
                <p v-else class="mt-1 text-xs text-muted-foreground">目前沒有已啟動的模型，請先至「模型」頁啟動。</p>
              </label>

              <!-- Compare: pick many models -->
              <div v-else class="block">
                <span class="text-xs text-muted-foreground">模型（可多選，僅顯示已就緒）</span>
                <div class="mt-1 max-h-48 space-y-1 overflow-y-auto rounded-md border border-input bg-background/40 p-2">
                  <label
                    v-for="o in modelOptions"
                    :key="o.value"
                    class="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-muted/50"
                  >
                    <input
                      type="checkbox"
                      :checked="isSelected(o.value)"
                      class="size-3.5 accent-[var(--chart-1)]"
                      @change="toggleLane(o.value)"
                    />
                    <span class="truncate font-mono text-xs">{{ o.label }}</span>
                    <Badge v-if="o.isLora" variant="outline" class="shrink-0 text-[10px]">LoRA</Badge>
                  </label>
                  <p v-if="!modelOptions.length" class="px-1 text-xs text-muted-foreground">目前沒有已啟動的模型。</p>
                </div>
              </div>

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
                <span class="text-xs text-muted-foreground">
                  模型（{{ isRerankMode ? '重排序' : '嵌入' }}，僅顯示已就緒）
                </span>
                <select
                  v-if="embModelOptions.length"
                  v-model="embModel"
                  class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
                >
                  <option v-for="o in embModelOptions" :key="o" :value="o">{{ o }}</option>
                </select>
                <p v-else class="mt-1 text-xs text-muted-foreground">
                  {{ embeddingReady ? '此模式無可用模型。' : 'embedding server 未啟動，請先至「模型」頁啟動。' }}
                </p>
              </label>
              <label class="block">
                <span class="text-xs text-muted-foreground">查詢（設定後進入重排序模式）</span>
                <Input v-model="rerankQuery" placeholder="留空則為純嵌入模式" class="mt-1" />
              </label>
              <label class="block">
                <span class="text-xs text-muted-foreground">輸入（每行一筆）</span>
                <Textarea v-model="embInput" class="mt-1 min-h-[140px] font-mono text-xs" />
              </label>
              <Button :disabled="embBusy || !embModel" @click="runEmbedding">
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
