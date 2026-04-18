import { NavLink } from 'react-router-dom'
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

function Layout({ children }) {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="sidebar-title">OCDJ</span>
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
          <span className="version">v2.0</span>
        </div>
      </aside>
      <main className="main-content">
        {children}
      </main>
    </div>
  )
}

export default Layout
