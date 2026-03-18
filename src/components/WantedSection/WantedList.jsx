import { useState } from 'react'
import {
  useWantedItems, useWantedSources, useCreateWantedItem,
  useUpdateWantedItem, useDeleteWantedItem, useBulkUpdateStatus,
  useBulkDeleteItems, useAddToQueue, useSearch,
} from '../../api/hooks'
import AddItemForm from './AddItemForm'
import ImportPanel from './ImportPanel'
import './WantedList.css'

const STATUS_LABELS = {
  pending: 'Pending',
  identified: 'Identified',
  searching: 'Searching',
  found: 'Found',
  downloading: 'Downloading',
  downloaded: 'Downloaded',
  tagged: 'Tagged',
  organized: 'Organized',
  not_found: 'Not Found',
  failed: 'Failed',
}

const STATUS_COLORS = {
  pending: 'var(--accent-amber)',
  identified: '#a78bfa',
  searching: '#60a5fa',
  found: 'var(--accent-green)',
  downloading: '#34d399',
  downloaded: '#10b981',
  tagged: '#8b5cf6',
  organized: '#6366f1',
  not_found: 'var(--accent-red)',
  failed: '#f87171',
}

function StatusBadge({ status }) {
  const color = STATUS_COLORS[status] || 'var(--text-muted)'
  return (
    <span
      className="status-badge-sm"
      style={{
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
        color: color,
      }}
    >
      {STATUS_LABELS[status] || status}
    </span>
  )
}

function EditItemForm({ item, sources, onSubmit, onClose }) {
  const [artist, setArtist] = useState(item.artist || '')
  const [title, setTitle] = useState(item.title || '')
  const [releaseName, setReleaseName] = useState(item.release_name || '')
  const [catalogNumber, setCatalogNumber] = useState(item.catalog_number || '')
  const [label, setLabel] = useState(item.label || '')
  const [sourceId, setSourceId] = useState(item.source || '')
  const [notes, setNotes] = useState(item.notes || '')
  const [itemStatus, setItemStatus] = useState(item.status || 'pending')

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!artist && !title && !releaseName && !catalogNumber) return
    onSubmit({
      id: item.id,
      artist,
      title,
      release_name: releaseName,
      catalog_number: catalogNumber,
      label,
      source: sourceId || null,
      notes,
      status: itemStatus,
    })
  }

  const hasAny = artist || title || releaseName || catalogNumber

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit Item</h3>
          <button className="btn-close" onClick={onClose} aria-label="Close" />
        </div>
        <form onSubmit={handleSubmit} className="add-form">
          <div className="form-row">
            <div className="form-group form-group--flex">
              <label>Artist</label>
              <input
                type="text"
                value={artist}
                onChange={(e) => setArtist(e.target.value)}
                placeholder="Artist name"
                autoFocus
              />
            </div>
            <div className="form-group form-group--flex">
              <label>Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Track title"
              />
            </div>
          </div>

          <div className="form-group">
            <label>Release</label>
            <input
              type="text"
              value={releaseName}
              onChange={(e) => setReleaseName(e.target.value)}
              placeholder="Album or EP name"
            />
          </div>

          <div className="form-row">
            <div className="form-group form-group--flex">
              <label>Catalog #</label>
              <input
                type="text"
                value={catalogNumber}
                onChange={(e) => setCatalogNumber(e.target.value)}
                placeholder="e.g. WARP123"
              />
            </div>
            <div className="form-group form-group--flex">
              <label>Label</label>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label name"
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group form-group--flex">
              <label>Source</label>
              <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">None</option>
                {Array.isArray(sources) && sources.map(s => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div className="form-group form-group--flex">
              <label>Status</label>
              <select value={itemStatus} onChange={(e) => setItemStatus(e.target.value)}>
                {Object.entries(STATUS_LABELS).map(([val, lbl]) => (
                  <option key={val} value={val}>{lbl}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="form-group">
            <label>Notes</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional notes"
            />
          </div>

          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={!hasAny}>
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function WantedList() {
  const [filters, setFilters] = useState({})
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [showAddForm, setShowAddForm] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [editingItem, setEditingItem] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [queuingId, setQueuingId] = useState(null)

  const { data, isLoading } = useWantedItems({
    ...filters,
    search: searchQuery || undefined,
  })
  const { data: sourcesData } = useWantedSources()
  const createItem = useCreateWantedItem()
  const updateItem = useUpdateWantedItem()
  const deleteItem = useDeleteWantedItem()
  const bulkUpdate = useBulkUpdateStatus()
  const bulkDelete = useBulkDeleteItems()
  const addToQueue = useAddToQueue()
  const searchSlsk = useSearch()

  const items = data?.results || []
  const sources = sourcesData?.results || sourcesData || []

  const toggleSelect = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === items.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(items.map(i => i.id)))
    }
  }

  const handleBulkAction = (action) => {
    const ids = Array.from(selectedIds)
    if (!ids.length) return

    if (action === 'delete') {
      if (confirm(`Delete ${ids.length} items?`)) {
        bulkDelete.mutate({ ids })
        setSelectedIds(new Set())
      }
    } else {
      bulkUpdate.mutate({ ids, status: action })
      setSelectedIds(new Set())
    }
  }

  const handleAddToQueue = (itemId) => {
    setQueuingId(itemId)
    addToQueue.mutate(
      { wanted_item_ids: [itemId] },
      {
        onSuccess: (data) => {
          // Auto-search each created queue item
          const queueItems = Array.isArray(data) ? data : []
          queueItems.forEach(qi => {
            if (qi.id && qi.status === 'pending') {
              searchSlsk.mutate({ queue_item_id: qi.id })
            }
          })
        },
        onSettled: () => setQueuingId(null),
      },
    )
  }

  const handleBulkAddToQueue = () => {
    const ids = Array.from(selectedIds)
    if (!ids.length) return
    addToQueue.mutate(
      { wanted_item_ids: ids },
      {
        onSuccess: (data) => {
          const queueItems = Array.isArray(data) ? data : []
          queueItems.forEach(qi => {
            if (qi.id && qi.status === 'pending') {
              searchSlsk.mutate({ queue_item_id: qi.id })
            }
          })
        },
      },
    )
    setSelectedIds(new Set())
  }

  const handleEdit = (formData) => {
    updateItem.mutate(formData, {
      onSuccess: () => setEditingItem(null),
    })
  }

  return (
    <div className="wanted-list">
      <div className="wanted-header">
        <h2 className="page-title">Wanted List</h2>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button className="btn btn-accent" onClick={() => setShowImport(true)}>
            Import
          </button>
          <button className="btn btn-primary" onClick={() => setShowAddForm(true)}>
            + Add
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="filters-row">
        <input
          type="text"
          className="search-input"
          placeholder="Search artist or title..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <select
          className="filter-select"
          value={filters.status || ''}
          onChange={(e) => setFilters(f => ({ ...f, status: e.target.value || undefined }))}
        >
          <option value="">All Statuses</option>
          {Object.entries(STATUS_LABELS).map(([val, lbl]) => (
            <option key={val} value={val}>{lbl}</option>
          ))}
        </select>
        <select
          className="filter-select"
          value={filters.source || ''}
          onChange={(e) => setFilters(f => ({ ...f, source: e.target.value || undefined }))}
        >
          <option value="">All Sources</option>
          {Array.isArray(sources) && sources.map(s => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </div>

      {/* Bulk Actions */}
      {selectedIds.size > 0 && (
        <div className="bulk-actions">
          <span className="bulk-count">{selectedIds.size} selected</span>
          <button className="btn btn-sm btn-accent" onClick={handleBulkAddToQueue}>Add to Queue</button>
          <button className="btn btn-sm" onClick={() => handleBulkAction('pending')}>Set Pending</button>
          <button className="btn btn-sm btn-danger" onClick={() => handleBulkAction('delete')}>Delete</button>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="loading">Loading...</div>
      ) : (
        <table className="wanted-table">
          <thead>
            <tr>
              <th className="th-check">
                <input
                  type="checkbox"
                  checked={items.length > 0 && selectedIds.size === items.length}
                  onChange={toggleSelectAll}
                />
              </th>
              <th>Artist</th>
              <th>Title</th>
              <th>Release</th>
              <th>Status</th>
              <th>Score</th>
              <th>Added</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => (
              <tr key={item.id} className={selectedIds.has(item.id) ? 'row-selected' : ''}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(item.id)}
                    onChange={() => toggleSelect(item.id)}
                  />
                </td>
                <td className="td-artist">{item.artist || '—'}</td>
                <td className="td-title">{item.title || '—'}</td>
                <td className="td-release">
                  {item.release_name || item.catalog_number || '—'}
                  {item.release_name && item.catalog_number && (
                    <span className="td-catalog"> {item.catalog_number}</span>
                  )}
                </td>
                <td><StatusBadge status={item.status} /></td>
                <td className="td-score">
                  {item.best_match_score ? `${item.best_match_score}%` : '—'}
                </td>
                <td className="td-date">
                  {new Date(item.added).toLocaleDateString('pt-BR')}
                </td>
                <td className="td-actions">
                  <button
                    className="btn btn-xs"
                    onClick={() => setEditingItem(item)}
                    title="Edit item"
                  >
                    Edit
                  </button>
                  <button
                    className={`btn btn-xs btn-accent${queuingId === item.id ? ' btn-xs--active' : ''}`}
                    onClick={() => handleAddToQueue(item.id)}
                    disabled={queuingId !== null}
                    title="Add to Soulseek Queue"
                  >
                    {queuingId === item.id ? 'Adding...' : 'Queue'}
                  </button>
                  <button
                    className="btn btn-xs btn-danger"
                    onClick={() => {
                      deleteItem.mutate(item.id)
                    }}
                    title="Delete"
                  >
                    Del
                  </button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan="8" className="empty-state">
                  No items yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {/* Pagination info */}
      {data?.count && (
        <div className="pagination-info">
          Showing {items.length} of {data.count} items
        </div>
      )}

      {/* Add Form Modal */}
      {showAddForm && (
        <AddItemForm
          sources={sources}
          onSubmit={(formData) => {
            createItem.mutate(formData)
            setShowAddForm(false)
          }}
          onClose={() => setShowAddForm(false)}
        />
      )}

      {/* Import Modal */}
      {showImport && (
        <ImportPanel onClose={() => setShowImport(false)} />
      )}

      {/* Edit Form Modal */}
      {editingItem && (
        <EditItemForm
          item={editingItem}
          sources={sources}
          onSubmit={handleEdit}
          onClose={() => setEditingItem(null)}
        />
      )}
    </div>
  )
}

export default WantedList
