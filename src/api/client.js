const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'
const DEFAULT_TIMEOUT_MS = 30000

async function request(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`
  const { headers: optionHeaders, timeout = DEFAULT_TIMEOUT_MS, signal: externalSignal, ...restOptions } = options

  const controller = new AbortController()
  const timeoutId = timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort()
    else externalSignal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  const config = {
    ...restOptions,
    signal: controller.signal,
    headers: {
      'Content-Type': 'application/json',
      ...optionHeaders,
    },
  }

  let response
  try {
    response = await fetch(url, config)
  } catch (err) {
    if (err.name === 'AbortError') {
      const error = new Error(`Request timed out after ${timeout}ms: ${endpoint}`)
      error.status = 0
      error.data = null
      throw error
    }
    throw err
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }

  if (!response.ok) {
    const error = new Error(`API Error: ${response.status} ${response.statusText}`)
    error.status = response.status
    try {
      error.data = await response.json()
    } catch {
      error.data = null
    }
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

export const api = {
  get: (endpoint, opts = {}) => request(endpoint, opts),
  post: (endpoint, data, opts = {}) => request(endpoint, { ...opts, method: 'POST', body: JSON.stringify(data) }),
  put: (endpoint, data, opts = {}) => request(endpoint, { ...opts, method: 'PUT', body: JSON.stringify(data) }),
  patch: (endpoint, data, opts = {}) => request(endpoint, { ...opts, method: 'PATCH', body: JSON.stringify(data) }),
  delete: (endpoint, data, opts = {}) => request(endpoint, { ...opts, method: 'DELETE', body: data ? JSON.stringify(data) : undefined }),
}

export default api
