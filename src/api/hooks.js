import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from './client'

// ── Wanted Sources ──────────────────────────────────────────

export function useWantedSources() {
  return useQuery({
    queryKey: ['wanted-sources'],
    queryFn: () => api.get('/wanted/sources/'),
  })
}

export function useCreateSource() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/wanted/sources/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-sources'] }),
  })
}

// ── Wanted Items ────────────────────────────────────────────

export function useWantedItems(params = {}) {
  const searchParams = new URLSearchParams()
  if (params.status) searchParams.set('status', params.status)
  if (params.source) searchParams.set('source', params.source)
  if (params.search) searchParams.set('search', params.search)
  if (params.page) searchParams.set('page', params.page)
  if (params.ordering) searchParams.set('ordering', params.ordering)

  const qs = searchParams.toString()
  return useQuery({
    queryKey: ['wanted-items', params],
    queryFn: () => api.get(`/wanted/items/${qs ? '?' + qs : ''}`),
    refetchInterval: (query) => {
      const data = query.state.data
      const items = data?.results || []
      if (items.some(i => i.status === 'searching')) return 3000
      return false
    },
  })
}

export function useCreateWantedItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/wanted/items/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-items'] }),
  })
}

export function useUpdateWantedItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...data }) => api.patch(`/wanted/items/${id}/`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-items'] }),
  })
}

export function useDeleteWantedItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.delete(`/wanted/items/${id}/`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-items'] }),
  })
}

export function useBulkAddWantedItems() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/wanted/items/bulk_add/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-items'] }),
  })
}

export function useBulkUpdateStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/wanted/items/bulk_update_status/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-items'] }),
  })
}

export function useBulkDeleteItems() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.delete('/wanted/items/bulk_delete/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wanted-items'] }),
  })
}

// ── Dashboard Stats ─────────────────────────────────────────

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: () => api.get('/core/stats/'),
    refetchInterval: 30000,
  })
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api.get('/core/health/'),
  })
}

// ── Soulseek Search Queue ───────────────────────────────────

export function useSearchQueue(params = {}) {
  const searchParams = new URLSearchParams()
  if (params.status) searchParams.set('status', params.status)
  if (params.ordering) searchParams.set('ordering', params.ordering)

  const qs = searchParams.toString()
  return useQuery({
    queryKey: ['search-queue', params],
    queryFn: () => api.get(`/soulseek/queue/${qs ? '?' + qs : ''}`),
    // Fast poll when items are searching
    refetchInterval: (query) => {
      const data = query.state.data
      const items = data?.results || []
      if (items.some(i => i.status === 'searching')) return 3000
      return false
    },
  })
}

export function useAddToQueue() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/soulseek/queue/add/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['search-queue'] }),
  })
}

export function useRemoveFromQueue() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.delete(`/soulseek/queue/${id}/`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['search-queue'] }),
  })
}

export function useClearQueue() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mode = 'downloaded') => api.post('/soulseek/queue/clear/', { mode }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['search-queue'] }),
  })
}

// ── Soulseek Search + Downloads ─────────────────────────────

export function useSlskdHealth() {
  return useQuery({
    queryKey: ['slskd-health'],
    queryFn: () => api.get('/soulseek/health/'),
    retry: false,
  })
}

export function useSearch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/soulseek/search/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['search-queue'] })
      qc.invalidateQueries({ queryKey: ['wanted-items'] })
    },
  })
}

export function useSearchResults(queueItemId) {
  return useQuery({
    queryKey: ['search-results', queueItemId],
    queryFn: () => api.get(`/soulseek/search/results/?queue_item_id=${queueItemId}`),
    enabled: !!queueItemId,
  })
}

export function useRecentSearches() {
  return useQuery({
    queryKey: ['recent-searches'],
    queryFn: () => api.get('/soulseek/search/recent/'),
  })
}

export function useDownloadFile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/soulseek/download/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['search-queue'] })
      qc.invalidateQueries({ queryKey: ['wanted-items'] })
      qc.invalidateQueries({ queryKey: ['downloads'] })
    },
  })
}

export function useDownloadsStatus() {
  const qc = useQueryClient()
  return useQuery({
    queryKey: ['downloads'],
    queryFn: async () => {
      const data = await api.get('/soulseek/downloads/')
      // Refresh both queue and wanted items so status badges stay in sync
      qc.invalidateQueries({ queryKey: ['search-queue'] })
      qc.invalidateQueries({ queryKey: ['wanted-items'] })
      return data
    },
    retry: false,
    refetchInterval: (query) => {
      const data = query.state.data
      const dls = data?.downloads || []
      if (dls.some(d => d.status === 'queued' || d.status === 'downloading')) return 3000
      return 30000
    },
  })
}

export function useCancelDownload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (downloadId) => api.post('/soulseek/downloads/cancel/', { download_id: downloadId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] })
      qc.invalidateQueries({ queryKey: ['search-queue'] })
      qc.invalidateQueries({ queryKey: ['wanted-items'] })
    },
  })
}

export function useClearDownloads() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mode = 'completed') => api.post('/soulseek/downloads/clear/', { mode }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['downloads'] }),
  })
}

// ── Config (Settings) ──────────────────────────────────────

export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: () => api.get('/core/config/'),
  })
}

export function useUpdateConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/core/config/update/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] })
      qc.invalidateQueries({ queryKey: ['import-config-status'] })
      qc.invalidateQueries({ queryKey: ['spotify-status'] })
    },
  })
}

// ── Wanted Imports ─────────────────────────────────────────

export function useImportOperations() {
  return useQuery({
    queryKey: ['import-operations'],
    queryFn: () => api.get('/wanted/import/operations/'),
    refetchInterval: (query) => {
      const data = query.state.data
      const ops = data?.results || []
      if (ops.some(o => o.status === 'fetching' || o.status === 'importing')) return 3000
      return false
    },
  })
}

export function useImportOperation(id) {
  return useQuery({
    queryKey: ['import-operation', id],
    queryFn: () => api.get(`/wanted/import/operations/${id}/`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.status === 'fetching' || data?.status === 'pending') return 2000
      return false
    },
  })
}

export function useTriggerImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/wanted/import/trigger/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['import-operations'] }),
  })
}

export function useConfirmImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, items }) => api.post(`/wanted/import/operations/${id}/confirm/`, { items }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['import-operations'] })
      qc.invalidateQueries({ queryKey: ['wanted-items'] })
      qc.invalidateQueries({ queryKey: ['wanted-sources'] })
    },
  })
}

export function useImportConfigStatus() {
  return useQuery({
    queryKey: ['import-config-status'],
    queryFn: () => api.get('/wanted/import/config-status/'),
    staleTime: 60000,
  })
}

export function useSpotifyStatus() {
  return useQuery({
    queryKey: ['spotify-status'],
    queryFn: () => api.get('/wanted/import/spotify/status/'),
    staleTime: 60000,
  })
}

// ── TraxDB ──────────────────────────────────────────────────

export function useTraxDBInventory() {
  return useQuery({
    queryKey: ['traxdb-inventory'],
    queryFn: () => api.get('/traxdb/inventory/'),
    staleTime: 60000, // 1 min — directory scan is lightweight but no need to spam it
  })
}

export function useTraxDBOperations(params = {}) {
  const searchParams = new URLSearchParams()
  if (params.op_type) searchParams.set('op_type', params.op_type)
  if (params.status) searchParams.set('status', params.status)

  const qs = searchParams.toString()
  return useQuery({
    queryKey: ['traxdb-operations', params],
    queryFn: () => api.get(`/traxdb/operations/${qs ? '?' + qs : ''}`),
    refetchInterval: (query) => {
      const data = query.state.data
      const ops = data?.results || []
      if (ops.some(o => o.status === 'running')) return 5000
      return false
    },
  })
}

export function useTraxDBOperation(id) {
  return useQuery({
    queryKey: ['traxdb-operation', id],
    queryFn: () => api.get(`/traxdb/operations/${id}/`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.status === 'running') return 3000
      return false
    },
  })
}

export function useTriggerSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data = {}) => api.post('/traxdb/sync/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['traxdb-operations'] }),
  })
}

export function useTriggerDownload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data = {}) => api.post('/traxdb/download/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['traxdb-operations'] }),
  })
}

export function useTriggerAudit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data = {}) => api.post('/traxdb/audit/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['traxdb-operations'] }),
  })
}

export function useTraxDBDownloadProgress(id) {
  return useQuery({
    queryKey: ['traxdb-download-progress', id],
    queryFn: () => api.get(`/traxdb/download/${id}/progress/`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.status === 'running') return 3000
      return false
    },
  })
}

// ── Recognize ────────────────────────────────────────────────

export function useRecognizeJobs() {
  return useQuery({
    queryKey: ['recognize-jobs'],
    queryFn: () => api.get('/recognize/jobs/'),
    refetchInterval: (query) => {
      const data = query.state.data
      const jobs = data?.results || []
      if (jobs.some(j => j.status === 'downloading' || j.status === 'recognizing')) return 5000
      return false
    },
  })
}

export function useRecognizeJob(id) {
  return useQuery({
    queryKey: ['recognize-job', id],
    queryFn: () => api.get(`/recognize/jobs/${id}/`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.status === 'downloading' || data?.status === 'recognizing') return 3000
      return false
    },
  })
}

export function useCreateRecognizeJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.post('/recognize/jobs/create/', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recognize-jobs'] }),
  })
}

export function useResumeRecognizeJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.post(`/recognize/jobs/${id}/resume/`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recognize-jobs'] }),
  })
}

export function useDeleteRecognizeJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.delete(`/recognize/jobs/${id}/delete/`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recognize-jobs'] }),
  })
}

export function useACRCloudUsage() {
  return useQuery({
    queryKey: ['acrcloud-usage'],
    queryFn: () => api.get('/recognize/acrcloud-usage/'),
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.active_jobs?.length > 0) return 10000
      return 60000
    },
  })
}

export function useAddRecognizeToWanted() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, track_indices }) =>
      api.post(`/recognize/jobs/${id}/add-to-wanted/`, { track_indices }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['recognize-jobs'] })
      qc.invalidateQueries({ queryKey: ['wanted-items'] })
    },
  })
}

export function useCancelTraxDBDownload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.post(`/traxdb/download/${id}/cancel/`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['traxdb-operations'] }),
  })
}

// ── Organize Pipeline ────────────────────────────────────────

export function usePipelineStats() {
  return useQuery({
    queryKey: ['pipeline-stats'],
    queryFn: () => api.get('/organize/pipeline/stats/'),
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.tagging > 0 || data?.renaming > 0) return 3000
      return 30000
    },
  })
}

export function usePipelineItems(params = {}) {
  const searchParams = new URLSearchParams()
  if (params.stage) searchParams.set('stage', params.stage)
  if (params.page) searchParams.set('page', params.page)

  const qs = searchParams.toString()
  return useQuery({
    queryKey: ['pipeline-items', params],
    queryFn: () => api.get(`/organize/pipeline/${qs ? '?' + qs : ''}`),
    refetchInterval: (query) => {
      const data = query.state.data
      const items = data?.results || []
      if (items.some(i => i.stage === 'tagging' || i.stage === 'renaming')) return 3000
      return false
    },
  })
}

export function useProcessPipeline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/organize/pipeline/process/'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}

export function useProcessSingle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.post(`/organize/pipeline/${id}/process/`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}

export function useRetryItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.post(`/organize/pipeline/${id}/retry/`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}

export function useSkipStage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.post(`/organize/pipeline/${id}/skip/`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}

export function useUpdatePipelineItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...data }) => api.patch(`/organize/pipeline/${id}/`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}

export function useRetagItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => api.post(`/organize/pipeline/${id}/retag/`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}

export function useScanDownloads() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/organize/pipeline/scan/'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pipeline-stats'] })
      qc.invalidateQueries({ queryKey: ['pipeline-items'] })
    },
  })
}
