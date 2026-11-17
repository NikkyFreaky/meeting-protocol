<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  Activity, ArrowLeft, ArrowRight, AudioLines,
  CircleAlert, FileAudio, FileText, FolderOpen, LoaderCircle, Plus, UploadCloud, X,
} from '@lucide/vue'
import { request, uploadMeeting } from './api.js'
import { clock, fileSize, meetingDate, stageNames, statusNames } from './format.js'
import MeetingDetail from './MeetingDetail.vue'

const defaultExtensions = ['.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.opus', '.mp4', '.webm', '.mov', '.mkv']
const config = ref({ max_upload_mb: 250, extensions: defaultExtensions, demo_enabled: true })
const meetings = ref([])
const meeting = ref(null)
const selectedId = ref(null)
const meetingsLoaded = ref(false)
const fileInput = ref(null)
const history = ref(null)
const uploadArea = ref(null)
const selectedFile = ref(null)
const title = ref('')
const speakerCount = ref('')
const dragging = ref(false)
const busy = ref('')
const progress = ref(0)
const error = ref('')
const historyError = ref('')
const loadingMeeting = ref(false)
let polling = false
let timer
let dragDepth = 0

const extensions = computed(() => config.value.extensions?.length ? config.value.extensions : defaultExtensions)
const stages = Object.entries(stageNames)
const stageIndex = computed(() => stages.findIndex(([id]) => id === meeting.value?.stage))
const activeMeeting = computed(() => !!selectedId.value)

function setFile(file) {
  if (!file || busy.value) return
  error.value = ''
  if (!extensions.value.some((ext) => file.name.toLowerCase().endsWith(ext.toLowerCase()))) {
    error.value = 'Этот формат не поддерживается. Выберите один из форматов, указанных ниже.'
    return
  }
  if (!file.size || file.size > config.value.max_upload_mb * 1024 * 1024) {
    error.value = `Выберите файл размером до ${config.value.max_upload_mb} МБ.`
    return
  }
  selectedFile.value = file
  title.value = file.name.replace(/\.[^.]+$/, '').slice(0, 200)
}

function chooseFile(event) {
  setFile(event.target.files[0])
  event.target.value = ''
}

function onDragEnter() {
  dragDepth += 1
  dragging.value = true
}

function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  dragging.value = dragDepth > 0
}

function onDrop(event) {
  dragDepth = 0
  dragging.value = false
  if (event.dataTransfer.files.length > 1) {
    error.value = 'Загружайте по одной записи за раз.'
    return
  }
  setFile(event.dataTransfer.files[0])
}

function clearFile() {
  selectedFile.value = null
  title.value = ''
  speakerCount.value = ''
}

function upsertMeeting(data) {
  const exists = meetings.value.some((item) => item.id === data.id)
  meetings.value = exists ? meetings.value.map((item) => item.id === data.id ? data : item) :
    [data, ...meetings.value]
}

async function loadMeetings() {
  try {
    meetings.value = await request('/meetings')
    if (meeting.value) upsertMeeting(meeting.value)
    meetingsLoaded.value = true
    historyError.value = ''
  } catch (err) {
    historyError.value = err.message
  }
}

async function openMeeting(id, initial = null) {
  selectedId.value = id
  loadingMeeting.value = !initial
  meeting.value = initial
  error.value = ''
  window.scrollTo({ top: 0, behavior: 'instant' })
  try {
    const data = initial?.protocol ? initial : await request(`/meetings/${id}`)
    if (selectedId.value === id) {
      meeting.value = data
      upsertMeeting(data)
    }
  } catch (err) {
    if (selectedId.value === id) error.value = err.message
  } finally {
    if (selectedId.value === id) loadingMeeting.value = false
  }
}

async function goHome(target) {
  selectedId.value = null
  meeting.value = null
  error.value = ''
  await nextTick()
  if (target === 'history') history.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  else if (target === 'upload') uploadArea.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  else window.scrollTo({ top: 0, behavior: 'instant' })
}

async function submit() {
  if (busy.value || !selectedFile.value) return
  busy.value = 'upload'
  error.value = ''
  progress.value = 0
  const data = new FormData()
  data.append('file', selectedFile.value)
  if (title.value.trim()) data.append('title', title.value.trim())
  if (speakerCount.value) data.append('speaker_count', speakerCount.value)
  try {
    const result = await uploadMeeting(data, (value) => { progress.value = value })
    clearFile()
    await openMeeting(result.id, result)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

async function openDemo() {
  if (busy.value) return
  busy.value = 'demo'
  error.value = ''
  try {
    const result = await request('/demo', { method: 'POST' })
    await openMeeting(result.id, result)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

async function refreshInProgress() {
  const current = meeting.value
  const inProgress = ['queued', 'processing']
  const currentIsActive = inProgress.includes(current?.status)
  if (polling || (!currentIsActive && !meetings.value.some((item) => inProgress.includes(item.status)))) return
  polling = true
  try {
    if (currentIsActive) {
      const result = await request(`/meetings/${current.id}`)
      if (current === meeting.value) meeting.value = result
      upsertMeeting(result)
    }
    if (meetings.value.some((item) => (!currentIsActive || item.id !== current.id) && inProgress.includes(item.status))) {
      await loadMeetings()
    }
  } catch {
    // Retry on the next tick after a transient connection error.
  } finally {
    polling = false
  }
}

onMounted(() => {
  request('/config').then((data) => { config.value = data }).catch(() => {})
  loadMeetings()
  timer = window.setInterval(refreshInProgress, 1800)
})
onUnmounted(() => window.clearInterval(timer))

function updateMeeting(data) {
  if (selectedId.value === data.id) meeting.value = data
  upsertMeeting(data)
}
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <button class="brand" type="button" @click="goHome()">
        <span class="brand-icon"><AudioLines :size="22" :stroke-width="2.4" /></span>
        <span>Протокол</span>
      </button>
      <p class="sidebar-label">Рабочее пространство</p>
      <nav class="navigation" aria-label="Основное меню">
        <button class="nav-button active" type="button" @click="goHome('history')">
          <FolderOpen :size="18" /> <span>Совещания</span>
        </button>
        <button class="nav-button nav-new" type="button" @click="goHome('upload')">
          <Plus :size="19" /> <span>Новая запись</span>
        </button>
      </nav>
      <div class="sidebar-foot">
        <span class="side-status-dot"></span>
        <span>Локальное приложение</span>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="breadcrumbs">
          <button type="button" @click="goHome()">Совещания</button>
          <template v-if="activeMeeting">
            <span aria-hidden="true">/</span>
            <span class="crumb-current">{{ meeting?.title || 'Запись' }}</span>
          </template>
        </div>
        <button v-if="activeMeeting" class="button button-small button-secondary" type="button" @click="goHome('upload')">
          <Plus :size="17" /> Новая запись
        </button>
      </header>

      <main class="workspace">
        <template v-if="!activeMeeting">
          <section class="page-intro">
            <h1>Совещания</h1>
            <p>Загрузите запись, чтобы получить расшифровку по говорящим, решения и поручения.</p>
          </section>
          <section ref="uploadArea" class="upload-panel card" aria-labelledby="upload-title">
            <header class="card-heading">
              <span class="heading-icon"><UploadCloud :size="21" /></span>
              <div>
                <h2 id="upload-title">Новая запись</h2>
                <p>Аудио или видео с совещания</p>
              </div>
            </header>
            <input ref="fileInput" type="file" class="visually-hidden" tabindex="-1" :accept="extensions.join(',')" @change="chooseFile" />
            <div
              v-if="!selectedFile" class="drop-zone" :class="{ dragging }"
              @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave.prevent="onDragLeave" @drop.prevent="onDrop"
            >
              <span class="drop-icon"><FileAudio :size="28" :stroke-width="1.7" /></span>
              <p><button class="text-button" type="button" :disabled="!!busy" @click="fileInput?.click()">Выберите файл</button> или перетащите его сюда</p>
              <small>{{ extensions.map((ext) => ext.replace(/^\./, '').toUpperCase()).join(', ') }} · до {{ config.max_upload_mb }} МБ</small>
            </div>
            <form v-else class="upload-form" @submit.prevent="submit">
              <div class="selected-file">
                <span class="drop-icon small"><FileAudio :size="20" /></span>
                <div><strong>{{ selectedFile.name }}</strong><small>{{ fileSize(selectedFile.size) }}</small></div>
                <button class="icon-button" type="button" aria-label="Убрать файл" :disabled="!!busy" @click="clearFile"><X :size="18" /></button>
              </div>
              <div class="form-row">
                <label>Название встречи <input v-model="title" maxlength="200" :disabled="!!busy" /></label>
                <label>Число говорящих <input v-model="speakerCount" type="number" min="1" max="20" placeholder="Автоматически" :disabled="!!busy" /></label>
              </div>
              <button class="button button-primary" type="submit" :disabled="!!busy">
                <LoaderCircle v-if="busy === 'upload'" class="spin" :size="17" />
                <ArrowRight v-else :size="17" />
                {{ busy === 'upload' ? `Загрузка · ${progress}%` : 'Начать обработку' }}
              </button>
            </form>
            <p v-if="error" class="error-message" role="alert"><CircleAlert :size="17" /> {{ error }}</p>
            <div v-if="config.demo_enabled" class="demo-strip">
              <span class="demo-icon"><Activity :size="17" /></span>
              <span><strong>Пример протокола</strong><small>Готовая расшифровка, решения и задачи</small></span>
              <button class="button button-small button-secondary" type="button" :disabled="!!busy" @click="openDemo">
                {{ busy === 'demo' ? 'Открывается…' : 'Открыть пример' }} <ArrowRight :size="15" />
              </button>
            </div>
          </section>

          <section ref="history" class="history" aria-labelledby="history-title">
            <div class="section-heading">
              <h2 id="history-title">История записей</h2>
              <span v-if="meetings.length" class="count-pill">{{ meetings.length }}</span>
            </div>
            <div v-if="historyError" class="card history-empty">
              <CircleAlert :size="22" /><p>{{ historyError }}</p>
              <button type="button" class="button button-small button-secondary" @click="loadMeetings">Обновить</button>
            </div>
            <div v-else-if="!meetingsLoaded" class="card history-empty">
              <LoaderCircle class="spin" :size="22" /><p>Загружаем историю…</p>
            </div>
            <div v-else-if="!meetings.length" class="card history-empty">
              <span class="drop-icon small"><FolderOpen :size="21" /></span>
              <div><strong>Записей пока нет</strong><small>Загруженные совещания появятся здесь.</small></div>
            </div>
            <div v-else class="history-list card">
              <button v-for="item in meetings" :key="item.id" class="history-item" type="button" @click="openMeeting(item.id)">
                <span class="item-icon"><FileText :size="20" /></span>
                <span class="history-item-name">
                  <strong>{{ item.title }}</strong>
                  <small>{{ meetingDate(item.created_at) }}<template v-if="item.duration"> · {{ clock(item.duration) }}</template><template v-if="item.demo"> · Пример</template></small>
                </span>
                <span :class="['status-pill', item.status]">{{ statusNames[item.status] }}</span>
                <ArrowRight class="history-arrow" :size="17" />
              </button>
            </div>
          </section>
        </template>

        <template v-else>
          <button type="button" class="back-link" @click="goHome('history')"><ArrowLeft :size="17" /> Все совещания</button>
          <p v-if="loadingMeeting" class="inline-info"><LoaderCircle class="spin" :size="19" /> Загружаем совещание…</p>
          <p v-if="error" class="error-message" role="alert"><CircleAlert :size="18" /> {{ error }}</p>
          <MeetingDetail v-if="meeting?.status === 'completed'" :key="meeting.id" :meeting="meeting"
            :integration-enabled="config.integration_enabled" @updated="updateMeeting" />
          <template v-else-if="meeting">
            <section class="page-intro">
              <h1>{{ meeting.title }}</h1>
              <p>{{ meeting.filename }}</p>
            </section>
            <section v-if="meeting.status === 'failed'" class="card processing">
              <span class="heading-icon failure"><CircleAlert :size="22" /></span>
              <h2>Не удалось обработать запись</h2>
              <p>{{ meeting.error || 'Загрузите запись повторно.' }}</p>
              <button class="button button-secondary" type="button" @click="goHome('upload')"><UploadCloud :size="17" /> Загрузить запись</button>
            </section>
            <section v-else class="card processing" aria-live="polite">
              <span class="heading-icon"><LoaderCircle :size="22" class="spin" /></span>
              <h2>{{ meeting.status === 'queued' ? 'Запись в очереди' : (stageNames[meeting.stage] || 'Обработка записи') }}</h2>
              <p>Вы можете вернуться к записи через историю совещаний.</p>
              <ol class="stage-list">
                <li v-for="([id, name], index) in stages" :key="id"
                  :class="{ 'stage-current': index === stageIndex, 'stage-done': index < stageIndex }">
                  <span></span>{{ name }}
                </li>
              </ol>
            </section>
          </template>
        </template>
      </main>
    </div>
  </div>
</template>
