<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CheckCircle2 } from '@lucide/vue'
import { api } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { lorasOfGroup } from '@/composables/useModelOptions'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardTitle from '@/components/ui/CardTitle.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Tabs from '@/components/ui/Tabs.vue'
import TabsList from '@/components/ui/TabsList.vue'
import TabsTrigger from '@/components/ui/TabsTrigger.vue'
import TabsContent from '@/components/ui/TabsContent.vue'
import Badge from '@/components/ui/Badge.vue'
import CodeBlock from '@/components/CodeBlock.vue'

const models = useModelsStore()
const base = api.routerBase

const groups = computed(() => [...new Set(models.llms.map((m) => m.key.split('::')[0] ?? m.key))])
// LoRA adapters across all groups — selectable in the chat dropdown so the code
// snippets switch to a LoRA call just by changing the `model` field.
const loraOptions = computed(() => {
  const out: { value: string; group: string }[] = []
  for (const g of groups.value) {
    for (const l of lorasOfGroup(models, g)) out.push({ value: l.name, group: g })
  }
  return out
})
const model = ref('')
const stream = ref(false)
const tab = ref('curl')

watch(
  groups,
  (gs) => {
    if (!model.value && gs.length) {
      const ready = models.llms.find((m) => m.state === 'ready')
      model.value = ready ? (ready.key.split('::')[0] ?? gs[0]!) : gs[0]!
    }
  },
  { immediate: true },
)

const embModel = computed(() => models.config?.embedding_server?.embedding_models?.[0] ?? 'm3e-base')
const rerankModel = computed(
  () => models.config?.embedding_server?.reranking_models?.[0] ?? 'bge-reranker-large',
)

// ---- Chat snippet generators (per language, honouring the stream toggle) ----
const m = computed(() => model.value || 'Qwen3-0.6B')
const jsonBool = computed(() => (stream.value ? 'true' : 'false'))
const pyBool = computed(() => (stream.value ? 'True' : 'False'))

const curl = computed(
  () =>
    `curl ${base}/v1/chat/completions \\
  -H "Content-Type: application/json" \\${stream.value ? '\n  -N \\' : ''}
  -d '{
    "model": "${m.value}",
    "messages": [{"role": "user", "content": "你好，請自我介紹"}],
    "stream": ${jsonBool.value}
  }'`,
)

const python = computed(
  () => `from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="not-needed")  # Router 不驗證金鑰

resp = client.chat.completions.create(
    model="${m.value}",
    messages=[{"role": "user", "content": "你好，請自我介紹"}],
    stream=${pyBool.value},
)
${
  stream.value
    ? `for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="", flush=True)`
    : `print(resp.choices[0].message.content)`
}`,
)

const requests = computed(
  () => `import requests

resp = requests.post(
    "${base}/v1/chat/completions",
    json={
        "model": "${m.value}",
        "messages": [{"role": "user", "content": "你好，請自我介紹"}],
        "stream": ${pyBool.value},
    },
    stream=${pyBool.value},
)
${
  stream.value
    ? `for line in resp.iter_lines():
    if line and line.startswith(b"data: "):
        data = line[6:]
        if data == b"[DONE]":
            break
        print(data.decode(), flush=True)`
    : `print(resp.json()["choices"][0]["message"]["content"])`
}`,
)

const js = computed(
  () => `const res = await fetch("${base}/v1/chat/completions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "${m.value}",
    messages: [{ role: "user", content: "你好，請自我介紹" }],
    stream: ${jsonBool.value},
  }),
});
${
  stream.value
    ? `const reader = res.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  process.stdout.write(decoder.decode(value)); // 內含 SSE \`data:\` 行
}`
    : `const data = await res.json();
console.log(data.choices[0].message.content);`
}`,
)

const node = computed(
  () => `import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${base}/v1", apiKey: "not-needed" });

const resp = await client.chat.completions.create({
  model: "${m.value}",
  messages: [{ role: "user", content: "你好，請自我介紹" }],
  stream: ${jsonBool.value},
});
${
  stream.value
    ? `for await (const chunk of resp) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}`
    : `console.log(resp.choices[0].message.content);`
}`,
)

const langs = [
  { value: 'curl', label: 'cURL', code: curl },
  { value: 'python', label: 'Python (SDK)', code: python },
  { value: 'requests', label: 'Python (requests)', code: requests },
  { value: 'js', label: 'JavaScript (fetch)', code: js },
  { value: 'node', label: 'Node (SDK)', code: node },
]

// ---- LoRA snippets ----
const exampleBase = computed(() => loraOptions.value[0]?.group ?? 'Qwen3-0.6B')
const exampleLora = computed(() => loraOptions.value[0]?.value ?? 'qwen3-test-lora')
const modelsCurl = computed(
  () => `curl ${base}/v1/models
# 帶 "parent" 的項目就是 LoRA（指向它的 base 模型）：
# {"id": "${exampleBase.value}", "object": "model"}
# {"id": "${exampleLora.value}", "object": "model", "parent": "${exampleBase.value}"}`,
)
const loraListPy = computed(
  () => `import requests

models = requests.get("${base}/v1/models").json()["data"]
loras = [m["id"] for m in models if m.get("parent")]
print(loras)  # 這些名稱可直接填進 chat 請求的 "model" 欄位`,
)

// ---- Embedding / rerank snippets ----
const embCurl = computed(
  () => `curl ${base}/v1/embeddings \\
  -H "Content-Type: application/json" \\
  -d '{"model": "${embModel.value}", "input": ["第一段文字", "第二段文字"]}'`,
)
const embPython = computed(
  () => `from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="not-needed")
resp = client.embeddings.create(
    model="${embModel.value}",
    input=["第一段文字", "第二段文字"],
)
print(len(resp.data[0].embedding))  # 向量維度`,
)
const rerankCurl = computed(
  () => `# 帶 query 欄位即切換為 reranking，回傳每個候選的相關性分數
curl ${base}/v1/embeddings \\
  -H "Content-Type: application/json" \\
  -d '{"model": "${rerankModel.value}", "query": "如何重置密碼？", "input": ["候選文件 A", "候選文件 B"]}'`,
)
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- Quick start -->
    <Card>
      <CardHeader>
        <CardTitle>快速開始</CardTitle>
        <p class="text-xs text-muted-foreground">
          Router 是 OpenAI 相容的統一入口，請求的 <span class="font-mono">model</span> 欄位填「群組名」，Router 會自動選負載最低的實例。
        </p>
      </CardHeader>
      <CardContent class="space-y-2 text-sm">
        <p class="flex items-center gap-2">
          <CheckCircle2 class="size-4 text-status-ready" />
          確認 Router 已啟動：<span class="font-mono text-xs">{{ base }}</span>
        </p>
        <p class="flex items-center gap-2">
          <CheckCircle2 class="size-4 text-status-ready" />
          在 Models 頁把要用的模型 <span class="font-medium">Start</span> 到
          <Badge variant="ready">ready</Badge>
        </p>
        <p class="flex items-center gap-2">
          <CheckCircle2 class="size-4 text-status-ready" />
          用下方任一語言呼叫；金鑰可任意填（Router 不驗證）
        </p>
      </CardContent>
    </Card>

    <!-- Chat completions -->
    <Card>
      <CardHeader class="flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>Chat Completions</CardTitle>
        <div class="flex items-center gap-3">
          <label class="flex items-center gap-1.5 text-xs text-muted-foreground">
            模型
            <select
              v-model="model"
              class="h-8 rounded-md border border-input bg-background/40 px-2 text-sm text-foreground"
            >
              <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
              <optgroup v-if="loraOptions.length" label="LoRA">
                <option v-for="l in loraOptions" :key="l.value" :value="l.value">{{ l.group }} / {{ l.value }}</option>
              </optgroup>
            </select>
          </label>
          <label class="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input v-model="stream" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
            串流 (stream)
          </label>
        </div>
      </CardHeader>
      <CardContent>
        <p v-if="loraOptions.length" class="mb-3 text-xs text-muted-foreground">
          下拉選單裡的 <span class="font-medium text-[var(--chart-3)]">LoRA</span> 項：把
          <span class="font-mono">model</span> 換成它的 served name 即可，其餘請求完全不變。
        </p>
        <Tabs v-model="tab" class="space-y-3">
          <TabsList>
            <TabsTrigger v-for="l in langs" :key="l.value" :value="l.value">{{ l.label }}</TabsTrigger>
          </TabsList>
          <TabsContent v-for="l in langs" :key="l.value" :value="l.value">
            <CodeBlock :code="l.code.value" />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>

    <!-- LoRA adapters -->
    <Card>
      <CardHeader>
        <CardTitle>LoRA Adapters</CardTitle>
        <p class="text-xs text-muted-foreground">
          呼叫 LoRA 與一般模型相同，只把 <span class="font-mono">model</span> 改成 adapter 的 served name（例
          <span class="font-mono">{{ exampleLora }}</span>），Router 會路由到對應 base 模型的實例。
        </p>
      </CardHeader>
      <CardContent class="space-y-4">
        <div class="grid gap-4 lg:grid-cols-2">
          <div class="space-y-2">
            <p class="text-xs font-medium text-muted-foreground">列出可用模型 / LoRA · cURL</p>
            <CodeBlock :code="modelsCurl" />
          </div>
          <div class="space-y-2">
            <p class="text-xs font-medium text-muted-foreground">只挑出 LoRA · Python</p>
            <CodeBlock :code="loraListPy" />
          </div>
        </div>
        <div class="space-y-1 text-xs text-muted-foreground">
          <p>· 前提：base 模型需以 <span class="font-mono">enable_lora</span> 啟動，且 adapter 已掛載（config 靜態掛，或在模型詳情抽屜熱載入）。</p>
          <p>· 打一個未掛載的名稱會回 <span class="font-mono">404 — Model not found</span>。</p>
          <p>· Base vs LoRA A/B：同一請求只換 <span class="font-mono">model</span>；Playground 比較模式可並排對照。</p>
        </div>
      </CardContent>
    </Card>

    <!-- Embeddings & Rerank -->
    <Card>
      <CardHeader>
        <CardTitle>Embeddings & Rerank</CardTitle>
        <p class="text-xs text-muted-foreground">
          同一個 <span class="font-mono">/v1/embeddings</span> 端點：帶 <span class="font-mono">query</span> 欄位即切換為 reranking。
        </p>
      </CardHeader>
      <CardContent class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-2">
          <p class="text-xs font-medium text-muted-foreground">Embedding · cURL</p>
          <CodeBlock :code="embCurl" />
          <p class="text-xs font-medium text-muted-foreground">Embedding · Python</p>
          <CodeBlock :code="embPython" />
        </div>
        <div class="space-y-2">
          <p class="text-xs font-medium text-muted-foreground">Rerank · cURL</p>
          <CodeBlock :code="rerankCurl" />
        </div>
      </CardContent>
    </Card>
  </div>
</template>
