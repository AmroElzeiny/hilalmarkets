import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { captureFirstTouchAttribution, initializeAnalytics } from './analytics'
import './index.css'

initializeAnalytics()
captureFirstTouchAttribution()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
