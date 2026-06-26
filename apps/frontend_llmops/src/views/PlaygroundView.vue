<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Bot, Loader2, Send, Square, Trash2, User, X } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
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

const { t } = useI18n()
const models = useModelsStore()
const tab = ref('chat')

const { options: modelOptions } = useModelOptions()
const model = ref('')
const maxTokens = ref(256)
const temperature = ref(0.7)
const stream = ref(true)

watch(
  modelOptions,
  (opts) => {
    if (!opts.some((o) => o.value === model.value)) model.value = opts[0]?.value ?? ''
  },
  { immediate: true },
)

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
}

interface Usage {
  completion_tokens?: number
  total_tokens?: number
}

const messages = ref<ChatMsg[]>([])
const chatInput = ref('')
const systemPrompt = ref('')
const busy = ref(false)

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
let abortController: AbortController | null = null

interface CompareLane {
  model: string
  messages: ChatMsg[]
  busy: boolean
  latency: number | null
  ttft: number | null
  tps: number | null
  tokens: number | null
}

const compareMode = ref(false)
const lanes = ref<CompareLane[]>([])
const anyBusy = computed(() => (compareMode.value ? lanes.value.some((l) => l.busy) : busy.value))

function toggleLane(m: string) {
  const i = lanes.value.findIndex((l) => l.model === m)
  if (i >= 0) lanes.value.splice(i, 1)
  else {
    lanes.value.push({
      model: m,
      messages: [],
      busy: false,
      latency: null,
      ttft: null,
      tps: null,
      tokens: null,
    })
  }
}

function isSelected(m: string) {
  return lanes.value.some((l) => l.model === m)
}

function tokensPerSec(usage: Usage | null, latencyMs: number): number | null {
  if (!usage?.completion_tokens || latencyMs <= 0) return null
  return usage.completion_tokens / (latencyMs / 1000)
}

async function streamChatCompletion(
  modelId: string,
  history: ChatMsg[],
  assistant: ChatMsg,
  opts: { onFirstToken?: () => void; signal?: AbortSignal } = {},
): Promise<Usage | null> {
  const msgs: { role: string; content: string }[] = []
  if (systemPrompt.value.trim()) msgs.push({ role: 'system', content: systemPrompt.value.trim() })
  for (const msg of history) msgs.push({ role: msg.role, content: msg.content })

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
          if (json.usage) usage = json.usage
          const delta = json.choices?.[0]?.delta ?? {}
          const think = delta.reasoning ?? delta.reasoning_content ?? ''
          if (think) {
            opts.onFirstToken?.()
            assistant.reasoning = (assistant.reasoning ?? '') + think
          }
          const content = delta.content ?? ''
          if (content) {
            opts.onFirstToken?.()
            assistant.content += content
          }
        } catch {
          // ignore partial frames
        }
      }
    }
  } else {
    const json = await res.json()
    opts.onFirstToken?.()
    const msg = json.choices?.[0]?.message ?? {}
    const think = msg.reasoning ?? msg.reasoning_content
    if (think) assistant.reasoning = think
    assistant.content = msg.content ?? (think ? '' : t('playground.emptyResponse'))
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
      if (!assistant.content) messages.value.pop()
    } else {
      messages.value.pop()
      toast.error(t('playground.chatFailed'), { description: String(e) })
    }
  } finally {
    lastLatency.value = performance.now() - t0
    busy.value = false
  }
}

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
      toast.error(`${lane.model}: ${t('playground.chatFailed')}`, { description: String(e) })
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
  await Promise.allSettled(lanes.value.map((lane) => runLane(lane)))
}

function send() {
  abortController = new AbortController()
  if (compareMode.value) void sendCompare()
  else void sendChat()
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

const embModel = ref('')
const defaultEmbeddingInput = computed(() => `${t('usage.sampleInputA')}\n${t('usage.sampleInputB')}`)
const embInput = ref('')
watch(
  defaultEmbeddingInput,
  (next, prev) => {
    if (!embInput.value || embInput.value === prev) embInput.value = next
  },
  { immediate: true },
)
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
  const rerank = isRerankMode.value
  try {
    const path = rerank ? '/v1/rerank' : '/v1/embeddings'
    const body: Record<string, unknown> = rerank
      ? { model: embModel.value, query: rerankQuery.value.trim(), documents: inputs }
      : { model: embModel.value || 'm3e-base', input: inputs }
    const res = await api.routerFetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    const json = await res.json()
    if (rerank) {
      // /v1/rerank returns results sorted by descending relevance_score.
      const results = json.results ?? []
      embResult.value = results.map(
        (r: { index: number; relevance_score: number; document?: { text: string } }) => ({
          index: r.index,
          value: r.relevance_score,
          preview: r.document?.text ?? inputs[r.index] ?? '',
        }),
      )
    } else {
      const data = json.data ?? []
      embResult.value = data.map((d: { embedding: number[] }, i: number) => {
        if (Array.isArray(d.embedding)) embDim.value = d.embedding.length
        return { index: i, value: d.embedding.length, preview: inputs[i] ?? '' }
      })
    }
  } catch (e) {
    toast.error(t('playground.embedFailed'), { description: String(e) })
  } finally {
    embBusy.value = false
  }
}

const isRerankMode = computed(() => !!rerankQuery.value.trim())
const embeddingReady = computed(() => models.byKey.get('embedding::default')?.state === 'ready')
const embModelOptions = computed(() => {
  const server = models.config?.embedding_server
  if (!server) return []
  return isRerankMode.value ? Object.keys(server.reranking_models) : Object.keys(server.embedding_models)
})
watch(
  embModelOptions,
  (opts) => {
    if (!opts.includes(embModel.value)) embModel.value = opts[0] ?? ''
  },
  { immediate: true },
)

const embeddingModeLabel = computed(() =>
  isRerankMode.value ? t('playground.rerank') : t('playground.embed'),
)
const embeddingResultType = computed(() =>
  isRerankMode.value ? t('playground.relevanceScores') : t('playground.vectorDimensions'),
)
const embeddingEmptyHint = computed(() =>
  embeddingReady.value ? t('playground.noModelsForMode') : t('playground.embNotStarted'),
)
</script>

<template>
  <div class="p-6">
    <Tabs v-model="tab" class="space-y-4">
      <TabsList>
        <TabsTrigger value="chat"><Bot class="size-4" />{{ t('playground.chat') }}</TabsTrigger>
        <TabsTrigger value="embedding">{{ t('playground.embeddingRerank') }}</TabsTrigger>
      </TabsList>

      <TabsContent value="chat">
        <div class="grid gap-4 lg:grid-cols-[1fr_18rem]">
          <Card v-if="!compareMode" glass class="flex h-[calc(100vh-12rem)] flex-col">
            <div v-autoscroll class="flex-1 space-y-4 overflow-y-auto p-5">
              <div
                v-for="(msg, i) in messages"
                :key="i"
                class="flex gap-3"
                :class="msg.role === 'user' ? 'flex-row-reverse' : ''"
              >
                <div
                  class="flex size-7 shrink-0 items-center justify-center rounded-full"
                  :class="msg.role === 'user' ? 'bg-[var(--chart-1)] text-white' : 'bg-muted'"
                >
                  <User v-if="msg.role === 'user'" class="size-4" />
                  <Bot v-else class="size-4" />
                </div>
                <div
                  class="max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed"
                  :class="
                    msg.role === 'user'
                      ? 'bg-[var(--chart-1)] text-white'
                      : 'border border-border/60 bg-background/40'
                  "
                >
                  <details v-if="msg.role === 'assistant' && msg.reasoning" open class="mb-2">
                    <summary class="cursor-pointer text-xs text-muted-foreground">
                      {{ t('playground.thinkingProcess') }}
                    </summary>
                    <div class="mt-1 whitespace-pre-wrap border-l-2 border-border/60 pl-2 text-xs text-muted-foreground">
                      {{ msg.reasoning }}
                    </div>
                  </details>
                  {{ msg.content }}<span v-if="busy && i === messages.length - 1" class="animate-pulse">...</span>
                </div>
              </div>
              <div
                v-if="!messages.length"
                class="flex h-full flex-col items-center justify-center text-center text-muted-foreground"
              >
                <Bot class="size-10 opacity-30" />
                <p class="mt-3 text-sm">
                  {{ t('playground.sendPrompt', { model: model || t('common.model') }) }}
                </p>
              </div>
            </div>
            <div class="border-t border-border/70 p-4">
              <div class="flex items-end gap-2">
                <Textarea
                  v-model="chatInput"
                  :placeholder="t('playground.inputPlaceholder')"
                  class="min-h-[44px] resize-none"
                  @keydown.enter.exact.prevent="send"
                />
                <Button
                  v-if="busy"
                  variant="outline"
                  class="h-11"
                  :title="t('playground.stopGeneration')"
                  @click="stop"
                >
                  <Square class="size-4" />
                </Button>
                <Button v-else class="h-11" @click="send">
                  <Send class="size-4" />
                </Button>
              </div>
              <div class="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                <span v-if="lastLatency" class="tabular">
                  {{ t('common.latency') }} {{ formatLatency(lastLatency) }}
                </span>
                <span v-if="lastUsage?.completion_tokens" class="tabular">{{ lastUsage.completion_tokens }} tok</span>
                <span v-if="lastTps" class="tabular">{{ lastTps.toFixed(1) }} tok/s</span>
                <button
                  v-if="messages.length"
                  class="ml-auto flex items-center gap-1 hover:text-foreground"
                  @click="clearChat"
                >
                  <Trash2 class="size-3.5" />{{ t('common.clear') }}
                </button>
              </div>
            </div>
          </Card>

          <div v-else class="flex h-[calc(100vh-12rem)] flex-col gap-3">
            <div
              v-if="!lanes.length"
              class="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border/60 text-center text-muted-foreground"
            >
              <Bot class="size-10 opacity-30" />
              <p class="mt-3 text-sm">{{ t('playground.selectModelsHint') }}</p>
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
                    <Badge v-if="lane.ttft != null" variant="muted" class="tabular">
                      {{ t('playground.firstToken') }} {{ formatLatency(lane.ttft) }}
                    </Badge>
                    <Badge v-if="lane.latency != null" variant="muted" class="tabular">
                      {{ t('common.latency') }} {{ formatLatency(lane.latency) }}
                    </Badge>
                    <Badge v-if="lane.tps != null" variant="muted" class="tabular">{{ lane.tps.toFixed(1) }} tok/s</Badge>
                    <button
                      class="text-muted-foreground hover:text-foreground"
                      :title="t('common.remove')"
                      @click="toggleLane(lane.model)"
                    >
                      <X class="size-3.5" />
                    </button>
                  </div>
                </div>
                <div v-autoscroll class="flex-1 space-y-3 overflow-y-auto p-4">
                  <div
                    v-for="(msg, i) in lane.messages"
                    :key="i"
                    class="flex gap-2"
                    :class="msg.role === 'user' ? 'flex-row-reverse' : ''"
                  >
                    <div
                      class="flex size-6 shrink-0 items-center justify-center rounded-full"
                      :class="msg.role === 'user' ? 'bg-[var(--chart-1)] text-white' : 'bg-muted'"
                    >
                      <User v-if="msg.role === 'user'" class="size-3.5" />
                      <Bot v-else class="size-3.5" />
                    </div>
                    <div
                      class="max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed"
                      :class="
                        msg.role === 'user'
                          ? 'bg-[var(--chart-1)] text-white'
                          : 'border border-border/60 bg-background/40'
                      "
                    >
                      <details v-if="msg.role === 'assistant' && msg.reasoning" open class="mb-1.5">
                        <summary class="cursor-pointer text-xs text-muted-foreground">
                          {{ t('playground.thinkingProcess') }}
                        </summary>
                        <div class="mt-1 whitespace-pre-wrap border-l-2 border-border/60 pl-2 text-xs text-muted-foreground">
                          {{ msg.reasoning }}
                        </div>
                      </details>
                      {{ msg.content }}<span v-if="lane.busy && i === lane.messages.length - 1" class="animate-pulse">...</span>
                    </div>
                  </div>
                  <div
                    v-if="!lane.messages.length"
                    class="flex h-full items-center justify-center text-center text-xs text-muted-foreground"
                  >
                    {{ t('playground.waitingPrompt') }}
                  </div>
                </div>
              </Card>
            </div>

            <Card class="p-4">
              <div class="flex items-end gap-2">
                <Textarea
                  v-model="chatInput"
                  :placeholder="t('playground.comparePlaceholder')"
                  class="min-h-[44px] resize-none"
                  @keydown.enter.exact.prevent="send"
                />
                <Button
                  v-if="anyBusy"
                  variant="outline"
                  class="h-11"
                  :title="t('playground.stopGeneration')"
                  @click="stop"
                >
                  <Square class="size-4" />
                </Button>
                <Button v-else :disabled="!lanes.length" class="h-11" @click="send">
                  <Send class="size-4" />
                </Button>
              </div>
              <div class="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                <span>{{ t('playground.modelsParallel', { n: lanes.length }) }}</span>
                <button
                  v-if="lanes.some((lane) => lane.messages.length)"
                  class="ml-auto flex items-center gap-1 hover:text-foreground"
                  @click="clearCompare"
                >
                  <Trash2 class="size-3.5" />{{ t('common.clearAll') }}
                </button>
              </div>
            </Card>
          </div>

          <Card class="h-fit p-5">
            <p class="mb-4 text-sm font-semibold">{{ t('playground.params') }}</p>
            <div class="space-y-4 text-sm">
              <label class="flex items-center justify-between">
                <span class="text-xs text-muted-foreground">{{ t('playground.compareMode') }}</span>
                <input v-model="compareMode" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
              </label>

              <label class="block">
                <span class="text-xs text-muted-foreground">{{ t('playground.systemPrompt') }}</span>
                <Textarea
                  v-model="systemPrompt"
                  :placeholder="t('playground.systemPromptPlaceholder')"
                  class="mt-1 min-h-[60px] resize-none text-xs"
                />
              </label>

              <label v-if="!compareMode" class="block">
                <span class="text-xs text-muted-foreground">{{ t('playground.modelReady') }}</span>
                <select
                  v-if="modelOptions.length"
                  v-model="model"
                  class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
                >
                  <option v-for="option in modelOptions" :key="option.value" :value="option.value">
                    {{ option.isLora ? `${option.label}  (LoRA)` : option.label }}
                  </option>
                </select>
                <p v-else class="mt-1 text-xs text-muted-foreground">{{ t('playground.noReadyModels') }}</p>
              </label>

              <div v-else class="block">
                <span class="text-xs text-muted-foreground">{{ t('playground.modelMulti') }}</span>
                <div class="mt-1 max-h-48 space-y-1 overflow-y-auto rounded-md border border-input bg-background/40 p-2">
                  <label
                    v-for="option in modelOptions"
                    :key="option.value"
                    class="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-muted/50"
                  >
                    <input
                      type="checkbox"
                      :checked="isSelected(option.value)"
                      class="size-3.5 accent-[var(--chart-1)]"
                      @change="toggleLane(option.value)"
                    />
                    <span class="truncate font-mono text-xs">{{ option.label }}</span>
                    <Badge v-if="option.isLora" variant="outline" class="shrink-0 text-[10px]">LoRA</Badge>
                  </label>
                  <p v-if="!modelOptions.length" class="px-1 text-xs text-muted-foreground">
                    {{ t('playground.noReadyModelsShort') }}
                  </p>
                </div>
              </div>

              <label class="block">
                <span class="flex justify-between text-xs text-muted-foreground">
                  {{ t('playground.maxTokens') }}
                  <span class="tabular text-foreground">{{ maxTokens }}</span>
                </span>
                <input
                  v-model.number="maxTokens"
                  type="range"
                  min="16"
                  max="2048"
                  step="16"
                  class="mt-1 w-full accent-[var(--chart-1)]"
                />
              </label>
              <label class="block">
                <span class="flex justify-between text-xs text-muted-foreground">
                  {{ t('playground.temperature') }}
                  <span class="tabular text-foreground">{{ temperature.toFixed(1) }}</span>
                </span>
                <input
                  v-model.number="temperature"
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  class="mt-1 w-full accent-[var(--chart-1)]"
                />
              </label>
              <label class="flex items-center justify-between">
                <span class="text-xs text-muted-foreground">{{ t('playground.streamResponse') }}</span>
                <input v-model="stream" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
              </label>
            </div>
          </Card>
        </div>
      </TabsContent>

      <TabsContent value="embedding">
        <div class="grid gap-4 lg:grid-cols-2">
          <Card class="p-5">
            <p class="mb-4 text-sm font-semibold">{{ t('playground.request') }}</p>
            <div class="space-y-4 text-sm">
              <label class="block">
                <span class="text-xs text-muted-foreground">
                  {{ t('playground.modelLabel', { mode: embeddingModeLabel }) }}
                </span>
                <select
                  v-if="embModelOptions.length"
                  v-model="embModel"
                  class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
                >
                  <option v-for="option in embModelOptions" :key="option" :value="option">{{ option }}</option>
                </select>
                <p v-else class="mt-1 text-xs text-muted-foreground">{{ embeddingEmptyHint }}</p>
              </label>
              <label class="block">
                <span class="text-xs text-muted-foreground">{{ t('playground.queryLabel') }}</span>
                <Input v-model="rerankQuery" :placeholder="t('playground.queryPlaceholder')" class="mt-1" />
              </label>
              <label class="block">
                <span class="text-xs text-muted-foreground">{{ t('playground.inputLabel') }}</span>
                <Textarea v-model="embInput" class="mt-1 min-h-[140px] font-mono text-xs" />
              </label>
              <Button :disabled="embBusy || !embModel" @click="runEmbedding">
                <Loader2 v-if="embBusy" class="size-4 animate-spin" />
                {{ embeddingModeLabel }}
              </Button>
            </div>
          </Card>

          <Card class="p-5">
            <div class="mb-4 flex items-center gap-2">
              <p class="text-sm font-semibold">{{ t('playground.result') }}</p>
              <Badge v-if="isRerankMode" variant="default">rerank</Badge>
              <Badge v-else-if="embDim" variant="muted">dim {{ embDim }}</Badge>
            </div>
            <div v-if="embResult" class="space-y-2">
              <div
                v-for="result in embResult"
                :key="result.index"
                class="rounded-lg border border-border/60 bg-background/40 p-3"
              >
                <div class="flex items-center justify-between gap-3">
                  <span class="truncate text-sm">{{ result.preview }}</span>
                  <span class="shrink-0 tabular text-sm font-medium">
                    {{ isRerankMode ? result.value.toFixed(4) : t('playground.dimVector', { n: result.value }) }}
                  </span>
                </div>
                <div v-if="isRerankMode" class="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    class="h-full rounded-full bg-[var(--chart-2)]"
                    :style="{ width: `${Math.max(2, Math.min(100, result.value * 100))}%` }"
                  />
                </div>
              </div>
            </div>
            <p v-else class="py-10 text-center text-sm text-muted-foreground">
              {{ t('playground.runToSee', { type: embeddingResultType }) }}
            </p>
          </Card>
        </div>
      </TabsContent>
    </Tabs>
  </div>
</template>
