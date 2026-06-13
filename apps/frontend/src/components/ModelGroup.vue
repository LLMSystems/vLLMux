<template>
  <section>
    <h2 class="section-title">{{ title }}</h2>
    <div class="card-list">
      <ModelCard
        v-for="(model, key) in normalizedModels"
        :key="model.name || key"
        :model="model"
        @start="$emit('start', model.name || key)"
        @stop="$emit('stop', model.name || key)"
      >
        <template v-if="model.name === 'Embedding & reranking Server'" #details>
          <p v-if="model.embedding_models">
            <strong>Embedding models：</strong>{{ model.embedding_models.join(', ') }}
          </p>
          <p v-if="model.reranking_models">
            <strong>Rerankers：</strong>{{ model.reranking_models.join(', ') }}
          </p>
        </template>

      </ModelCard>
    </div>
  </section>
</template>

<script setup>
import ModelCard from './ModelCard.vue'
import { computed, defineProps } from 'vue'

const props = defineProps({
  title: String,
  models: {
    type: [Array, Object],
    required: true
  }
})
console.log('Props:', props)
const normalizedModels = computed(() => {
  return Array.isArray(props.models)
    ? props.models
    : Object.entries(props.models).map(([name, info]) => ({ name, ...info }))
})
console.log('Normalized Models:', normalizedModels.value)
</script>

<style scoped>
.section-title {
  font-size: 1.5rem;
  margin-top: 2rem;
  margin-bottom: 1rem;
}

.card-list {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  justify-content: flex-start;
}

@media (max-width: 600px) {
  .card-list > * {
    flex: 1 1 100%;
  }
}
</style>
