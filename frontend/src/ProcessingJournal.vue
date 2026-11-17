<script setup>
import { computed } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { stageNames } from './format.js'

const props = defineProps({ meeting: { type: Object, required: true } })
const timeFormat = new Intl.DateTimeFormat('ru-RU', {
  hour: '2-digit', minute: '2-digit', second: '2-digit',
})

const stages = computed(() => {
  const events = props.meeting.processing_events ?? []
  return Object.entries(stageNames).map(([id, name]) => {
    const entries = events.filter((entry) => entry.stage === id)
    let state = 'waiting'
    if (entries.some((entry) => entry.kind === 'error')) state = 'error'
    else if (entries.at(-1)?.kind === 'done') state = 'done'
    else if (props.meeting.status === 'processing' && props.meeting.stage === id) state = 'running'
    return { id, name, entries, state }
  })
})
const labels = { waiting: 'Ожидает', running: 'Идёт', done: 'Готово', error: 'Ошибка' }
const hasJournal = computed(() => (props.meeting.processing_events?.length ?? 0) > 0
  || ['queued', 'processing'].includes(props.meeting.status))
</script>

<template>
  <section class="card processing-journal" aria-labelledby="journal-title">
    <h2 id="journal-title">Журнал обработки</h2>
    <p v-if="!hasJournal" class="muted">Для этой записи журнал недоступен.</p>
    <ol v-else class="journal-stages">
      <li v-for="stage in stages" :key="stage.id">
        <details>
          <summary>
            <ChevronRight class="journal-chevron" :size="16" aria-hidden="true" />
            <span>{{ stage.name }}</span>
            <span :class="['journal-state', stage.state]">{{ labels[stage.state] }}</span>
          </summary>
          <ul v-if="stage.entries.length" class="journal-entries">
            <li v-for="entry in stage.entries" :key="entry.id" :class="{ 'journal-error': entry.kind === 'error' }">
              <time :datetime="entry.created_at">{{ timeFormat.format(new Date(entry.created_at)) }}</time>
              <span>{{ entry.message }}</span>
            </li>
          </ul>
          <p v-else class="journal-empty muted">
            {{ stage.state === 'running' ? 'Ожидаем первые события.' : 'Этап ещё не начался.' }}
          </p>
        </details>
      </li>
    </ol>
  </section>
</template>
