import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import './Layout.css'

// Grouped by workflow stage. Sidebar reflects the mental model:
//   Capture (Recognize, TraxDB) -> Curate (Wanted) -> Fetch (Soulseek) ->
//   Process (Organize) -> Library -> Settings.
const NAV_GROUPS = [
  {
    label: '',
    items: [
      { to: '/dashboard', label: 'Dashboard' },
      { to: '/agent', label: 'Agent' },
    ],
  },
  {
    label: 'Capture',
    items: [
      { to: '/recognize', label: 'Recognize' },
      { to: '/cratemate', label: 'Crate-Mate' },
      { to: '/traxdb', label: 'TraxDB' },
    ],
  },
  { label: 'Curate', items: [{ to: '/wanted', label: 'Wanted' }] },
  { label: 'Fetch', items: [{ to: '/soulseek', label: 'Soulseek' }] },
  { label: 'Process', items: [{ to: '/organize', label: 'Organize' }] },
  {
    label: 'Library',
    items: [
      { to: '/library', label: 'Library' },
      { to: '/settings', label: 'Settings' },
    ],
  },
]

// Map route → human title for the mobile header. Keeps the bar context-aware
// without exposing the full route string.
const ROUTE_TITLES = NAV_GROUPS.flatMap((g) => g.items).reduce((acc, it) => {
  acc[it.to] = it.label
  return acc
}, {})

function Layout({ children }) {
  // Drawer state is only meaningful on mobile, but we keep one state hook so
  // the markup is identical at all sizes — CSS handles the transform.
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()

  // Close the drawer on route change. Without this, tapping a nav item leaves
  // the overlay covering the new page on mobile.
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // Lock body scroll while the drawer is open so the page underneath doesn't
  // jitter when the user drags on the overlay.
  useEffect(() => {
    if (drawerOpen) {
      document.body.style.overflow = 'hidden'
      return () => { document.body.style.overflow = '' }
    }
  }, [drawerOpen])

  const currentTitle = ROUTE_TITLES[location.pathname] || 'OCDJ'

  return (
    <div className="layout">
      {/* Mobile-only top bar with hamburger + current route name. Hidden ≥768px
          via .mobile-only utility from index.css. */}
      <header className="topbar mobile-only">
        <button
          type="button"
          className="topbar__menu"
          aria-label="Open navigation"
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen(true)}
        >
          <span className="topbar__menu-line" aria-hidden="true" />
          <span className="topbar__menu-line" aria-hidden="true" />
          <span className="topbar__menu-line" aria-hidden="true" />
        </button>
        <span className="topbar__title">{currentTitle}</span>
        <span className="topbar__brand">OCDJ</span>
      </header>

      {/* Backdrop tappable to close. Pointer-events controlled by .open class. */}
      <div
        className={`drawer-backdrop ${drawerOpen ? 'drawer-backdrop--open' : ''}`}
        onClick={() => setDrawerOpen(false)}
        aria-hidden="true"
      />

      <aside className={`sidebar ${drawerOpen ? 'sidebar--open' : ''}`}>
        <div className="sidebar-header">
          <span className="sidebar-title">OCDJ</span>
          <button
            type="button"
            className="sidebar-close mobile-only"
            aria-label="Close navigation"
            onClick={() => setDrawerOpen(false)}
          >
            ×
          </button>
        </div>
        <nav className="sidebar-nav">
          {NAV_GROUPS.map((group, gi) => (
            <div key={gi} className="nav-group">
              {group.label && <span className="nav-group__label">{group.label}</span>}
              {group.items.map(({ to, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    `nav-item ${isActive ? 'nav-item--active' : ''}`
                  }
                >
                  {label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="version">v2.1 · mobile</span>
        </div>
      </aside>
      <main className="main-content">
        {children}
      </main>
    </div>
  )
}

export default Layout
