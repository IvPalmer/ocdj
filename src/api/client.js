const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

async function request(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`
  const { headers: optionHeaders, ...restOptions } = options
  const config = {
    ...restOptions,
    headers: {
      'Content-Type': 'application/json',
      ...optionHeaders,
    },
  }

  const response = await fetch(url, config)

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
  get: (endpoint) => request(endpoint),
  post: (endpoint, data) => request(endpoint, { method: 'POST', body: JSON.stringify(data) }),
  put: (endpoint, data) => request(endpoint, { method: 'PUT', body: JSON.stringify(data) }),
  patch: (endpoint, data) => request(endpoint, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (endpoint, data) => request(endpoint, { method: 'DELETE', body: data ? JSON.stringify(data) : undefined }),
}

export default api
