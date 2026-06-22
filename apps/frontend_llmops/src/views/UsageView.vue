<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CheckCircle2 } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
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

const { t } = useI18n()
const models = useModelsStore()
const base = api.routerBase

const groups = computed(() => [...new Set(models.llms.map((m) => m.key.split('::')[0] ?? m.key))])
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

const m = computed(() => model.value || 'Qwen3-0.6B')
const jsonBool = computed(() => (stream.value ? 'true' : 'false'))
const pyBool = computed(() => (stream.value ? 'True' : 'False'))
const samplePrompt = computed(() => JSON.stringify(t('usage.samplePrompt')))
const sampleInputs = computed(() =>
  [t('usage.sampleInputA'), t('usage.sampleInputB')].map((value) => JSON.stringify(value)).join(', '),
)
const sampleQuery = computed(() => JSON.stringify(t('usage.sampleQuery')))
const sampleDocs = computed(() =>
  [t('usage.sampleDocA'), t('usage.sampleDocB')].map((value) => JSON.stringify(value)).join(', '),
)

const curl = computed(
  () =>
    `curl ${base}/v1/chat/completions \\
  -H "Content-Type: application/json" \\${stream.value ? '\n  -N \\' : ''}
  -d '{
    "model": "${m.value}",
    "messages": [{"role": "user", "content": ${samplePrompt.value}}],
    "stream": ${jsonBool.value}
  }'`,
)

const python = computed(
  () => `from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="not-needed")  # ${t('usage.routerNoAuthComment')}
resp = client.chat.completions.create(
    model="${m.value}",
    messages=[{"role": "user", "content": ${samplePrompt.value}}],
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
        "messages": [{"role": "user", "content": ${samplePrompt.value}}],
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
    messages: [{ role: "user", content: ${samplePrompt.value} }],
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
  process.stdout.write(decoder.decode(value)); // ${t('usage.streamComment')}
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
  messages: [{ role: "user", content: ${samplePrompt.value} }],
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

const exampleBase = computed(() => loraOptions.value[0]?.group ?? 'Qwen3-0.6B')
const exampleLora = computed(() => loraOptions.value[0]?.value ?? 'qwen3-test-lora')
const modelsCurl = computed(
  () => `curl ${base}/v1/models
# ${t('usage.modelsComment')}
# {"id": "${exampleBase.value}", "object": "model"}
# {"id": "${exampleLora.value}", "object": "model", "parent": "${exampleBase.value}"}`,
)
const loraListPy = computed(
  () => `import requests

models = requests.get("${base}/v1/models").json()["data"]
loras = [m["id"] for m in models if m.get("parent")]
print(loras)  # ${t('usage.loraListComment')}`,
)

const embCurl = computed(
  () => `curl ${base}/v1/embeddings \\
  -H "Content-Type: application/json" \\
  -d '{"model": "${embModel.value}", "input": [${sampleInputs.value}]}'`,
)
const embPython = computed(
  () => `from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="not-needed")
resp = client.embeddings.create(
    model="${embModel.value}",
    input=[${sampleInputs.value}],
)
print(len(resp.data[0].embedding))  # ${t('usage.vectorLengthComment')}`,
)
const rerankCurl = computed(
  () => `# ${t('usage.rerankComment')}
curl ${base}/v1/embeddings \\
  -H "Content-Type: application/json" \\
  -d '{"model": "${rerankModel.value}", "query": ${sampleQuery.value}, "input": [${sampleDocs.value}]}'`,
)
</script>

<template>
  <div class="space-y-6 p-6">
    <Card>
      <CardHeader>
        <CardTitle>{{ t('usage.quickStart') }}</CardTitle>
        <p class="text-xs text-muted-foreground">{{ t('usage.quickStartDesc') }}</p>
      </CardHeader>
      <CardContent class="space-y-2 text-sm">
        <p class="flex items-center gap-2">
          <CheckCircle2 class="size-4 text-status-ready" />
          {{ t('usage.step1') }}<span class="font-mono text-xs">{{ base }}</span>
        </p>
        <p class="flex items-center gap-2">
          <CheckCircle2 class="size-4 text-status-ready" />
          {{ t('usage.step2') }}<span class="font-medium">{{ t('common.start') }}</span>
          {{ t('usage.step2End') }}
          <Badge variant="ready">{{ t('common.ready') }}</Badge>
        </p>
        <p class="flex items-center gap-2">
          <CheckCircle2 class="size-4 text-status-ready" />
          {{ t('usage.step3') }}
        </p>
      </CardContent>
    </Card>

    <Card>
      <CardHeader class="flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>{{ t('usage.chatCompletions') }}</CardTitle>
        <div class="flex items-center gap-3">
          <label class="flex items-center gap-1.5 text-xs text-muted-foreground">
            {{ t('usage.modelLabel') }}
            <select
              v-model="model"
              class="h-8 rounded-md border border-input bg-background/40 px-2 text-sm text-foreground"
            >
              <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
              <optgroup v-if="loraOptions.length" label="LoRA">
                <option v-for="l in loraOptions" :key="l.value" :value="l.value">
                  {{ l.group }} / {{ l.value }}
                </option>
              </optgroup>
            </select>
          </label>
          <label class="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input v-model="stream" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
            {{ t('usage.streamLabel') }}
          </label>
        </div>
      </CardHeader>
      <CardContent>
        <p v-if="loraOptions.length" class="mb-3 text-xs text-muted-foreground">
          {{ t('usage.loraDropdownHint') }}
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

    <Card>
      <CardHeader>
        <CardTitle>{{ t('usage.loraTitle') }}</CardTitle>
        <p class="text-xs text-muted-foreground">
          {{ t('usage.loraDesc') }}
          <span class="font-mono">{{ exampleLora }}</span>
          {{ t('usage.loraDescEnd') }}
        </p>
      </CardHeader>
      <CardContent class="space-y-4">
        <div class="grid gap-4 lg:grid-cols-2">
          <div class="space-y-2">
            <p class="text-xs font-medium text-muted-foreground">{{ t('usage.listModelsLabel') }}</p>
            <CodeBlock :code="modelsCurl" />
          </div>
          <div class="space-y-2">
            <p class="text-xs font-medium text-muted-foreground">{{ t('usage.filterLoraLabel') }}</p>
            <CodeBlock :code="loraListPy" />
          </div>
        </div>
        <div class="space-y-1 text-xs text-muted-foreground">
          <p>{{ t('usage.loraNote1') }}</p>
          <p>{{ t('usage.loraNote2') }}</p>
          <p>{{ t('usage.loraNote3') }}</p>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardHeader>
        <CardTitle>{{ t('usage.embTitle') }}</CardTitle>
        <p class="text-xs text-muted-foreground">{{ t('usage.embDesc') }}</p>
      </CardHeader>
      <CardContent class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-2">
          <p class="text-xs font-medium text-muted-foreground">{{ t('usage.embCurlLabel') }}</p>
          <CodeBlock :code="embCurl" />
          <p class="text-xs font-medium text-muted-foreground">{{ t('usage.embPyLabel') }}</p>
          <CodeBlock :code="embPython" />
        </div>
        <div class="space-y-2">
          <p class="text-xs font-medium text-muted-foreground">{{ t('usage.rerankCurlLabel') }}</p>
          <CodeBlock :code="rerankCurl" />
        </div>
      </CardContent>
    </Card>
  </div>
</template>
