import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout/Layout'
import Dashboard from './components/DashboardSection/Dashboard'
import WantedList from './components/WantedSection/WantedList'
import SoulseekPanel from './components/SoulseekSection/SoulseekPanel'
import TraxDBPanel from './components/TraxDBSection/TraxDBPanel'
import RecognizePanel from './components/RecognizeSection/RecognizePanel'
import OrganizePanel from './components/OrganizeSection/OrganizePanel'
import LibraryPanel from './components/LibrarySection/LibraryPanel'
import SettingsPanel from './components/SettingsSection/SettingsPanel'
import AgentPanel from './components/AgentSection/AgentPanel'
import './App.css'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/wanted" element={<WantedList />} />
        <Route path="/soulseek" element={<SoulseekPanel />} />
        <Route path="/traxdb" element={<TraxDBPanel />} />
        <Route path="/recognize" element={<RecognizePanel />} />
        <Route path="/organize" element={<OrganizePanel />} />
        <Route path="/library" element={<LibraryPanel />} />
        <Route path="/agent" element={<AgentPanel />} />
        <Route path="/settings" element={<SettingsPanel />} />
      </Routes>
    </Layout>
  )
}

export default App
