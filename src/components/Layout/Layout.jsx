import { NavLink } from 'react-router-dom'
import './Layout.css'

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/wanted', label: 'Wanted' },
  { to: '/soulseek', label: 'Soulseek' },
  { to: '/traxdb', label: 'TraxDB' },
  { to: '/recognize', label: 'Recognize' },
  { to: '/organize', label: 'Organize' },
  { to: '/library', label: 'Library' },
  { to: '/settings', label: 'Settings' },
]

function Layout({ children }) {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="sidebar-title">OCDJ</span>
        </div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map(({ to, label }) => (
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
