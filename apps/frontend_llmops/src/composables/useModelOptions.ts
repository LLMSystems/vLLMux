import { computed } from 'vue'
import { useModelsStore } from '@/stores/models'

/** One selectable inference target: a base group or one of its LoRA adapters.
 *
 * `value` is what goes in the request `model` field — the group key for a base
 * model, or the LoRA served name for an adapter (the router routes it over the
 * base group's instances). `group` is always the base group, so callers that
 * need the base's instances/lifecycle can resolve back to it. */
export interface ModelOption {
  value: string
  label: string
  group: string
  isLora: boolean
}

/** Ready model groups plus the LoRA adapters statically mounted on them.
 *
 * A group is offered when it has at least one ready instance; its LoRAs are read
 * from the deploy config (`settings.lora_modules`) and offered alongside it, so
 * the Playground/Eval/Benchmark pickers can target a base or any adapter. */
export function useModelOptions() {
  const models = useModelsStore()

  const readyGroups = computed(() => [
    ...new Set(
      models.llms.filter((m) => m.state === 'ready').map((m) => m.key.split('::')[0] ?? m.key),
    ),
  ])

  const options = computed<ModelOption[]>(() => {
    const out: ModelOption[] = []
    for (const group of readyGroups.value) {
      out.push({ value: group, label: group, group, isLora: false })
      for (const lora of lorasOfGroup(models, group)) {
        out.push({ value: lora.name, label: `${group} / ${lora.name}`, group, isLora: true })
      }
    }
    return out
  })

  return { options, readyGroups }
}

/** LoRA adapters configured on a base group. The deploy config is keyed by
 *  instance key (`group::instance`), so resolve the group to any of its entries
 *  (model_config is shared across a group's instances). */
export function lorasOfGroup(
  models: ReturnType<typeof useModelsStore>,
  group: string,
): { name: string; path: string }[] {
  const entry = Object.entries(models.config?.LLM_engines ?? {}).find(
    ([k]) => (k.split('::')[0] ?? k) === group,
  )?.[1]
  return (entry?.settings?.lora_modules ?? []).filter((l) => !!l?.name)
}
