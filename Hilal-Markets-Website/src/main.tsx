import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { SiteFooter } from './components/SiteChrome'
import { captureFirstTouchAttribution, initializeAnalytics } from './analytics'
import './index.css'

initializeAnalytics()
captureFirstTouchAttribution()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

// A server-rendered page that borrows the site's header and footer — the Market page —
// leaves a second place for the footer below its own content.
const footerRoot = document.getElementById('hm-site-footer')
if (footerRoot) {
  ReactDOM.createRoot(footerRoot).render(
    <React.StrictMode>
      <SiteFooter />
    </React.StrictMode>,
  )
}
