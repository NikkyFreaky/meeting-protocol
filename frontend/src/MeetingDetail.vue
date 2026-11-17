<script setup>
import { computed, ref } from 'vue'
import {
  ArrowDownToLine, ArrowUpRight, Captions, Check, ChevronRight, CircleAlert, ClipboardList,
  Clock3, FileText, ListTodo, LoaderCircle, Mic2, Pencil, Play, Plus, Send, Trash2, Users,
} from '@lucide/vue'
import { jsonRequest } from './api.js'
import { clock, meetingDate } from './format.js'

const props = defineProps({
  meeting: { type: Object, required: true },
  integrationEnabled: { type: Boolean, default: false },
})
const emit = defineEmits(['updated'])
const tab = ref('protocol')
const media = ref(null)
const pendingSeek = ref(null)
const mediaError = ref('')
const filter = ref('')
const draft = ref(null)
const working = ref('')
const actionError = ref('')
const feedback = ref('')
const checked = ref([])
const recentDeliveries = ref({})
const baseUrl = computed(() => `/api/meetings/${encodeURIComponent(props.meeting.id)}`)
const isVideo = computed(() => /\.(mp4|webm|mkv|mov|avi|m4v|mpeg|mpg)$/i.test(props.meeting.filename || ''))
const protocol = computed(() => draft.value?.protocol ?? props.meeting.protocol ??
  { summary: '', topics: [], decisions: [], tasks: [] })
const speakers = computed(() => draft.value?.speakers ?? props.meeting.speakers ?? {})
const segmentRows = computed(() => (props.meeting.segments || []).map((segment, index) =>
  ({ ...segment, index })).filter((segment) => !filter.value || segment.speaker === filter.value))
const speakerColors = computed(() => Object.fromEntries(
  Object.keys(speakers.value).map((id, index) => [id, index % 5])))
const sent = computed(() => ({
  ...Object.fromEntries((props.meeting.deliveries || []).map((item) => [item.task_id, item])),
  ...recentDeliveries.value,
}))
const unsentSelection = computed(() => checked.value.filter((id) => !sent.value[id]))

const tabs = [
  { id: 'protocol', label: 'Протокол', icon: ClipboardList },
  { id: 'transcript', label: 'Расшифровка', icon: Captions },
  { id: 'speakers', label: 'Говорящие', icon: Users },
]

function speakerName(id) {
  return speakers.value[id] || id || 'Не определён'
}

function switchTab(value) {
  tab.value = value
  actionError.value = ''
  feedback.value = ''
}

function jump(at) {
  if (!media.value || !Number.isFinite(at) || at < 0) return
  pendingSeek.value = at
  if (media.value.readyState >= 1) applySeek()
  else media.value.load()
}

function applySeek() {
  if (!media.value || pendingSeek.value === null) return
  media.value.currentTime = pendingSeek.value
  pendingSeek.value = null
  media.value.play().catch(() => {}) // The native control remains available if autoplay is restricted.
}

function beginEdit() {
  feedback.value = ''
  actionError.value = ''
  draft.value = JSON.parse(JSON.stringify({ protocol: protocol.value, speakers: speakers.value }))
}

function cancelEdit() {
  draft.value = null
  actionError.value = ''
}

function addTask() {
  draft.value.protocol.tasks.push({
    id: crypto.randomUUID(),
    title: '',
    assignee: '',
    due_date: '',
    at: media.value?.currentTime || 0,
  })
}

async function save() {
  if (!draft.value || working.value) return
  const data = JSON.parse(JSON.stringify(draft.value))
  data.protocol.summary = data.protocol.summary.trim()
  data.protocol.topics = data.protocol.topics.map((item) => item.trim()).filter(Boolean)
  data.protocol.decisions = data.protocol.decisions
    .map((item) => ({ ...item, text: item.text.trim() })).filter((item) => item.text)
  data.protocol.tasks = data.protocol.tasks.map((item) => ({
    ...item, title: item.title.trim(), assignee: item.assignee.trim(), due_date: item.due_date.trim(),
  }))
  if (data.protocol.tasks.some((item) => !item.title)) {
    actionError.value = 'Укажите название каждого поручения.'
    return
  }
  for (const [id, name] of Object.entries(data.speakers)) {
    data.speakers[id] = name.trim() || props.meeting.speakers[id] || id
  }
  actionError.value = ''
  working.value = 'save'
  try {
    const updated = await jsonRequest(`/meetings/${props.meeting.id}`, 'PATCH', data)
    checked.value = checked.value.filter((id) => updated.protocol.tasks.some((task) => task.id === id))
    emit('updated', updated)
    draft.value = null
    feedback.value = 'Изменения сохранены.'
  } catch (err) {
    actionError.value = err.message
  } finally {
    working.value = ''
  }
}

async function sendTasks() {
  if (working.value || !unsentSelection.value.length || !props.integrationEnabled || draft.value) return
  actionError.value = ''
  feedback.value = ''
  working.value = 'send'
  try {
    const result = await jsonRequest(`/meetings/${props.meeting.id}/tasks/send`, 'POST', {
      task_ids: unsentSelection.value,
    })
    for (const receipt of result.sent) recentDeliveries.value[receipt.task_id] = receipt
    checked.value = unsentSelection.value
    if (result.sent.length) feedback.value = `Отправлено в трекер: ${result.sent.length}.`
    if (result.failed.length) {
      actionError.value = result.failed.map((item) => {
        const title = protocol.value.tasks.find((task) => task.id === item.task_id)?.title
        return title ? `«${title}» — ${item.error}` : item.error
      }).join(' · ')
    }
  } catch (err) {
    actionError.value = err.message
  } finally {
    working.value = ''
  }
}
</script>

<template>
  <section class="page-intro detail-intro">
    <div class="detail-title">
      <h1>{{ meeting.title }}</h1>
      <p>
        <span class="status-pill completed"><Check :size="13" /> Готово</span>
        <span>{{ meetingDate(meeting.created_at) }}</span>
        <span v-if="meeting.duration"><Clock3 :size="14" /> {{ clock(meeting.duration) }}</span>
        <span v-if="meeting.demo">Пример</span>
      </p>
    </div>
    <details class="export-menu" v-if="!draft">
      <summary class="button button-secondary"><ArrowDownToLine :size="17" /> Скачать</summary>
      <div>
        <a :href="`${baseUrl}/export?format=md`" download>Протокол <small>MD</small></a>
        <a :href="`${baseUrl}/export?format=csv`" download>Поручения <small>CSV</small></a>
        <a :href="`${baseUrl}/export?format=json`" download>Все данные <small>JSON</small></a>
      </div>
    </details>
  </section>

  <section v-if="meeting.media_url" class="card media-card" aria-label="Запись совещания">
    <span class="heading-icon"><Mic2 :size="19" /></span>
    <div class="media-title"><strong>Запись совещания</strong><small>{{ meeting.filename }}</small></div>
    <component :is="isVideo ? 'video' : 'audio'" :key="meeting.media_url" ref="media" :src="meeting.media_url"
      controls preload="metadata" @loadedmetadata="applySeek"
      @error="mediaError = 'Браузер не может воспроизвести эту запись.'" />
    <p v-if="mediaError" class="error-message" role="alert"><CircleAlert :size="17" /> {{ mediaError }}</p>
  </section>

  <section class="card result-card" aria-label="Результаты обработки">
    <header class="result-heading">
      <nav class="tabs" aria-label="Разделы протокола">
        <button v-for="item in tabs" :key="item.id" type="button" :class="{ current: item.id === tab }"
          :aria-pressed="item.id === tab" @click="switchTab(item.id)">
          <component :is="item.icon" :size="17" /> {{ item.label }}
        </button>
      </nav>
      <div class="result-actions">
        <template v-if="draft">
          <button class="button button-small button-secondary" type="button" :disabled="!!working" @click="cancelEdit">Отмена</button>
          <button class="button button-small button-primary" type="button" :disabled="!!working" @click="save">
            <LoaderCircle v-if="working" class="spin" :size="16" /><Check v-else :size="16" /> Сохранить
          </button>
        </template>
        <button v-else-if="tab !== 'transcript'" class="button button-small button-secondary" type="button" :disabled="!!working" @click="beginEdit">
          <Pencil :size="15" /> Редактировать
        </button>
      </div>
    </header>
    <div v-if="actionError || feedback" class="result-messages">
      <p v-if="actionError" class="error-message" role="alert"><CircleAlert :size="17" /> {{ actionError }}</p>
      <p v-if="feedback" class="success-message" role="status"><Check :size="17" /> {{ feedback }}</p>
    </div>

    <div v-if="tab === 'protocol'" class="result-body protocol" :aria-busy="!!working">
      <section class="protocol-block">
        <h2><FileText :size="19" /> Краткое содержание</h2>
        <textarea v-if="draft" v-model="protocol.summary" rows="4" maxlength="30000" aria-label="Краткое содержание" :disabled="!!working"></textarea>
        <p v-else class="summary-text">{{ protocol.summary || 'Краткое содержание не сформировано.' }}</p>
      </section>
      <section class="protocol-block">
        <h2><ListTodo :size="19" /> Темы обсуждения</h2>
        <template v-if="draft">
          <div v-for="(_, index) in protocol.topics" :key="index" class="edit-line">
            <input v-model="protocol.topics[index]" maxlength="4000" :aria-label="`Тема ${index + 1}`" :disabled="!!working" />
            <button class="icon-button" type="button" aria-label="Удалить тему" :disabled="!!working" @click="protocol.topics.splice(index, 1)"><Trash2 :size="17" /></button>
          </div>
          <button type="button" class="button button-small button-secondary" :disabled="!!working" @click="protocol.topics.push('')"><Plus :size="15" /> Тема</button>
        </template>
        <ul v-else-if="protocol.topics.length" class="topics">
          <li v-for="(topic, index) in protocol.topics" :key="index">{{ topic }}</li>
        </ul>
        <p v-else class="muted">Темы не выделены.</p>
      </section>
      <section class="protocol-block">
        <h2><Check :size="19" /> Решения</h2>
        <template v-if="draft">
          <div v-for="(decision, index) in protocol.decisions" :key="index" class="edit-line">
            <input v-model="decision.text" maxlength="4000" :aria-label="`Решение ${index + 1}`" :disabled="!!working" />
            <button class="icon-button" type="button" aria-label="Удалить решение" :disabled="!!working" @click="protocol.decisions.splice(index, 1)"><Trash2 :size="17" /></button>
          </div>
          <button type="button" class="button button-small button-secondary" :disabled="!!working"
            @click="protocol.decisions.push({ text: '', at: media?.currentTime || 0 })"><Plus :size="15" /> Решение</button>
        </template>
        <ul v-else-if="protocol.decisions.length" class="decisions">
          <li v-for="(decision, index) in protocol.decisions" :key="index">
            <button v-if="meeting.media_url && decision.at != null" type="button" class="timestamp" @click="jump(decision.at)">
              <Play :size="12" /> {{ clock(decision.at) }}
            </button>
            <span>{{ decision.text }}</span>
          </li>
        </ul>
        <p v-else class="muted">Решения не выделены.</p>
      </section>
      <section class="protocol-block tasks-block">
        <div class="section-heading">
          <h2><ClipboardList :size="19" /> Поручения</h2>
          <button v-if="!draft && protocol.tasks.length" type="button" class="button button-small button-primary"
            :disabled="!integrationEnabled || !unsentSelection.length || !!working" @click="sendTasks">
            <LoaderCircle v-if="working === 'send'" class="spin" :size="15" /><Send v-else :size="15" />
            В трекер<template v-if="unsentSelection.length"> · {{ unsentSelection.length }}</template>
          </button>
        </div>
        <template v-if="draft">
          <div v-for="(task, index) in protocol.tasks" :key="task.id" class="task-edit">
            <label class="task-edit-title">Поручение <input v-model="task.title" maxlength="4000" :disabled="!!working" /></label>
            <label>Ответственный <input v-model="task.assignee" maxlength="200" :disabled="!!working" placeholder="Не указан" /></label>
            <label>Срок <input v-model="task.due_date" maxlength="200" :disabled="!!working" placeholder="ГГГГ-ММ-ДД" /></label>
            <button class="icon-button" type="button" aria-label="Удалить поручение" :disabled="!!working" @click="protocol.tasks.splice(index, 1)"><Trash2 :size="17" /></button>
          </div>
          <button type="button" class="button button-small button-secondary" :disabled="!!working" @click="addTask"><Plus :size="15" /> Поручение</button>
        </template>
        <template v-else-if="protocol.tasks.length">
          <p v-if="!integrationEnabled" class="muted integration-note">YouGile не настроен. Поручения можно скачать в CSV.</p>
          <ul class="task-list">
            <li v-for="task in protocol.tasks" :key="task.id">
              <input v-if="integrationEnabled && !sent[task.id]" v-model="checked" type="checkbox" :value="task.id"
                :aria-label="`Выбрать поручение «${task.title}»`" :disabled="!!working" />
              <Check v-else-if="sent[task.id]" class="sent-check" :size="17" />
              <div class="task-content">
                <strong>{{ task.title }}</strong>
                <small>{{ task.assignee || 'Без ответственного' }} · {{ task.due_date || 'Без срока' }}</small>
                <a v-if="sent[task.id]?.url" class="tracker-link" :href="sent[task.id].url" target="_blank" rel="noopener noreferrer">Открыть карточку <ArrowUpRight :size="14" /></a>
                <small v-else-if="sent[task.id]" class="sent-check" :title="sent[task.id].external_id">Отправлено в трекер</small>
              </div>
              <button v-if="meeting.media_url && task.at != null" type="button" class="timestamp" @click="jump(task.at)">
                <Play :size="12" /> {{ clock(task.at) }}
              </button>
            </li>
          </ul>
        </template>
        <p v-else class="muted">Поручения не выделены.</p>
      </section>
    </div>

    <div v-else-if="tab === 'transcript'" class="result-body">
      <div class="transcript-toolbar">
        <span>{{ segmentRows.length }} реплик</span>
        <select v-model="filter" aria-label="Фильтр по говорящим">
          <option value="">Все говорящие</option>
          <option v-for="(name, id) in speakers" :key="id" :value="id">{{ name || id }}</option>
        </select>
      </div>
      <p v-if="!segmentRows.length" class="muted">Здесь нет распознанных реплик.</p>
      <ol v-else class="transcript">
        <li v-for="segment in segmentRows" :key="segment.index">
          <button v-if="meeting.media_url" type="button" class="timestamp" @click="jump(segment.start)">
            <Play :size="12" /> {{ clock(segment.start) }}
          </button>
          <span v-else class="timestamp">{{ clock(segment.start) }}</span>
          <div>
            <strong :class="['speaker-chip', `speaker-${speakerColors[segment.speaker] ?? 0}`]">
              {{ speakerName(segment.speaker) }}
            </strong>
            <p>{{ segment.text }}</p>
          </div>
          <small class="segment-end">до {{ clock(segment.end) }}</small>
        </li>
      </ol>
    </div>

    <div v-else class="result-body speakers">
      <p class="muted">Задайте имена, чтобы они появились в расшифровке и экспорте.</p>
      <ul v-if="Object.keys(speakers).length" class="speaker-list">
        <li v-for="(name, id) in speakers" :key="id">
          <span :class="['speaker-avatar', `speaker-${speakerColors[id] ?? 0}`]"><Users :size="18" /></span>
          <input v-if="draft" v-model="speakers[id]" :aria-label="`Имя «${id}»`" maxlength="100" :disabled="!!working" />
          <strong v-else>{{ name || id }}</strong>
          <button type="button" class="speaker-snippet" @click="switchTab('transcript'); filter = id">
            Реплики <ChevronRight :size="15" />
          </button>
        </li>
      </ul>
      <p v-else class="muted">Говорящие не выделены.</p>
    </div>
  </section>
</template>
