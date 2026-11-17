export const stageNames = {
  decode: 'Подготовка аудиодорожки',
  diarize: 'Определение говорящих',
  transcribe: 'Расшифровка речи',
  protocol: 'Составление протокола',
}

export const statusNames = {
  queued: 'В очереди',
  processing: 'Обработка',
  completed: 'Готово',
  failed: 'Ошибка',
}

export function clock(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0))
  const minutes = Math.floor(value / 60) % 60
  const tail = [minutes, value % 60].map((part) => String(part).padStart(2, '0')).join(':')
  return value >= 3600 ? `${Math.floor(value / 3600)}:${tail}` : tail
}

export function meetingDate(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'short', year: 'numeric',
  }).format(date).replace(/ г\.$/, '')
}

export function fileSize(bytes) {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} КБ` :
    `${(bytes / (1024 * 1024)).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ`
}
