export function errorMessage(body, status) {
  if (typeof body?.detail === 'string') return body.detail
  if (Array.isArray(body?.detail)) return body.detail.map((item) => item.msg).join('. ')
  return `Сервер вернул ошибку (${status}).`
}

export async function request(path, options) {
  let response
  try {
    response = await fetch(`/api${path}`, options)
  } catch {
    throw new Error('Сервер недоступен. Проверьте, что приложение запущено.')
  }
  const body = await response.json().catch(() => null)
  if (!response.ok || body === null) throw new Error(errorMessage(body, response.status))
  return body
}

export function jsonRequest(path, method, data) {
  return request(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function uploadMeeting(data, progress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/meetings')
    xhr.responseType = 'json'
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300 && xhr.response) resolve(xhr.response)
      else reject(new Error(errorMessage(xhr.response, xhr.status)))
    }
    xhr.onerror = () => reject(new Error('Связь с сервером прервалась. Попробуйте загрузить файл ещё раз.'))
    xhr.send(data)
  })
}
