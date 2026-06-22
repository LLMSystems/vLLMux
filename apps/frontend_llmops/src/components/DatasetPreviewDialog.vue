<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Loader2 } from '@lucide/vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api, ApiError } from '@/lib/api'
import Dialog from '@/components/ui/Dialog.vue'
import Badge from '@/components/ui/Badge.vue'
import Tabs from '@/components/ui/Tabs.vue'
import TabsList from '@/components/ui/TabsList.vue'
import TabsTrigger from '@/components/ui/TabsTrigger.vue'
import TabsContent from '@/components/ui/TabsContent.vue'
import type { EvalDatasetPreview } from '@/types/api'

// Reusable dataset inspector: loads the first rows of an eval dataset subset
// straight from the cache. Used from both the Eval page and the Datasets library.
const props = defineProps<{ datasetKey: string; subset?: string }>()
const open = defineModel<boolean>('open', { default: false })

const data = ref<EvalDatasetPreview | null>(null)
const loading = ref(false)
const error = ref('')
const tab = ref<'intro' | 'samples'>('intro')

// Markdown intro -> sanitised HTML (descriptions come from evalscope's registry).
const descHtml = computed(() => {
  const d = data.value?.description
  if (!d) return ''
  return DOMPurify.sanitize(marked.parse(d, { async: false }) as string)
})

async function load() {
  if (!props.datasetKey) return
  loading.value = true
  data.value = null
  error.value = ''
  tab.value = 'intro'
  try {
    data.value = await api.getEvalDatasetPreview(props.datasetKey, props.subset, 20)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// Fetch whenever the dialog opens (or its target changes while open).
watch(
  () => [open.value, props.datasetKey, props.subset],
  ([isOpen]) => {
    if (isOpen) void load()
  },
  { immediate: true },
)
</script>

<template>
  <Dialog v-model:open="open" :title="$t('datasetPreview.title', { key: datasetKey })" width-class="max-w-3xl">
    <div class="mt-3">
      <div v-if="loading" class="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 class="size-4 animate-spin" />{{ $t('datasetPreview.loading') }}
      </div>
      <p v-else-if="error" class="rounded-md bg-status-failed/10 px-3 py-2 text-xs text-status-failed">
        {{ error }}
      </p>
      <Tabs v-else-if="data" v-model="tab" class="space-y-3">
        <TabsList>
          <TabsTrigger value="intro">{{ $t('datasetPreview.introTab') }}</TabsTrigger>
          <TabsTrigger value="samples">{{ $t('datasetPreview.samplesTab', { n: data.rows.length }) }}</TabsTrigger>
        </TabsList>

        <!-- Introduction -->
        <TabsContent value="intro" class="space-y-3">
          <div class="flex flex-wrap items-center gap-1.5">
            <span v-if="data.pretty_name" class="text-sm font-semibold">{{ data.pretty_name }}</span>
            <Badge v-for="t in data.tags ?? []" :key="t" variant="muted" class="text-[10px]">{{ t }}</Badge>
            <span v-if="data.metric?.length" class="text-[10px] text-muted-foreground">· {{ data.metric.join(', ') }}</span>
            <span v-if="(data.subsets?.length ?? 0) > 1" class="text-[10px] text-muted-foreground">· {{ $t('datasetPreview.subjectCount', { n: data.subsets!.length }) }}</span>
          </div>
          <div v-if="descHtml" class="markdown max-h-[60vh] overflow-y-auto text-sm" v-html="descHtml" />
          <p v-else class="text-xs text-muted-foreground">{{ $t('datasetPreview.noDescription') }}</p>
        </TabsContent>

        <!-- Sample rows -->
        <TabsContent value="samples" class="space-y-3">
          <p class="text-xs text-muted-foreground">
            subset <span class="font-mono text-foreground">{{ data.subset }}</span>
            ·
            <template v-if="data.truncated">{{ $t('datasetPreview.showingFirst', { n: data.rows.length }) }}</template>
            <template v-else>{{ $t('datasetPreview.totalRows', { n: data.count }) }}</template>
          </p>
          <div class="max-h-[60vh] space-y-2 overflow-y-auto">
            <div
              v-for="(r, i) in data.rows"
              :key="i"
              class="rounded-md border border-border/60 p-2.5 text-xs"
            >
              <div class="mb-1 flex items-center gap-2">
                <span class="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">#{{ i + 1 }}</span>
                <span v-if="r.target != null" class="text-[10px] text-[var(--chart-1)]">{{ $t('datasetPreview.answer') }}<span class="font-mono">{{ r.target }}</span></span>
              </div>
              <p class="whitespace-pre-wrap break-words text-foreground/90">{{ r.question }}</p>
              <ul v-if="r.choices.length" class="mt-1.5 space-y-0.5 text-muted-foreground">
                <li v-for="(c, ci) in r.choices" :key="ci" class="flex gap-1.5">
                  <span class="font-mono text-[10px]">{{ String.fromCharCode(65 + ci) }}.</span><span>{{ c }}</span>
                </li>
              </ul>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  </Dialog>
</template>

<style scoped>
/* Minimal markdown styling for the rendered intro (no typography plugin). */
.markdown :deep(h1),
.markdown :deep(h2),
.markdown :deep(h3) {
  font-weight: 600;
  margin: 0.6em 0 0.3em;
  line-height: 1.3;
}
.markdown :deep(h1) { font-size: 1.1rem; }
.markdown :deep(h2) { font-size: 1rem; }
.markdown :deep(h3) { font-size: 0.9rem; }
.markdown :deep(p) { margin: 0.4em 0; line-height: 1.6; }
.markdown :deep(ul),
.markdown :deep(ol) { margin: 0.4em 0; padding-left: 1.3em; list-style: revert; }
.markdown :deep(li) { margin: 0.15em 0; }
.markdown :deep(code) {
  font-family: var(--font-mono, monospace);
  font-size: 0.85em;
  background: var(--muted);
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
}
.markdown :deep(pre) {
  background: var(--muted);
  padding: 0.6em 0.8em;
  border-radius: 0.4rem;
  overflow-x: auto;
  margin: 0.5em 0;
}
.markdown :deep(pre code) { background: none; padding: 0; }
.markdown :deep(a) { color: var(--chart-1); text-decoration: underline; }
.markdown :deep(strong) { font-weight: 600; }
.markdown :deep(table) { border-collapse: collapse; margin: 0.5em 0; }
.markdown :deep(th),
.markdown :deep(td) { border: 1px solid var(--border); padding: 0.3em 0.6em; }
</style>
