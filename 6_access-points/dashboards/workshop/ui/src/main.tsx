import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// Self-hosted UI font (the cluster is offline, so no runtime CDN). Weights used by the
// chrome: 400 body · 600 semibold · 700 bold · 800 stat numerals.
import '@fontsource/nunito-sans/400.css';
import '@fontsource/nunito-sans/600.css';
import '@fontsource/nunito-sans/700.css';
import '@fontsource/nunito-sans/800.css';
// Self-hosted serif display and mono faces. A CDN font fails in the offline
// cluster and the serif headings would fall back to a system serif.
import '@fontsource/cormorant-garamond/500.css';
import '@fontsource/cormorant-garamond/600.css';
import '@fontsource/cormorant-garamond/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import './styles/base.css';
import './styles/home.css';
import './styles/runtime.css';
import './styles/widgets.css';
import './styles/x-cloak.css';
import App from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
