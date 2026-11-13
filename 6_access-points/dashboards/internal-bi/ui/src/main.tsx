import { createRoot } from 'react-dom/client';
// Self-hosted fonts (offline cluster: no runtime CDN). Nunito Sans is the UI
// body face and Cormorant Garamond the heading serif: the shared workshop face
// applied across every dashboard (global font decree). Inter is retained only
// for a few standalone screens that still hardcode it (auth splash, denied-access
// takeover); JetBrains Mono for code.
import '@fontsource/nunito-sans/400.css';
import '@fontsource/nunito-sans/600.css';
import '@fontsource/nunito-sans/700.css';
import '@fontsource/nunito-sans/800.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/inter/800.css';
import '@fontsource/cormorant-garamond/500.css';
import '@fontsource/cormorant-garamond/600.css';
import '@fontsource/cormorant-garamond/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import './styles/base.css';
import './styles/home.css';
import './styles/x-cloak.css';
import './styles/runtime.css';
import App from './App';
import { installDashboardApi } from './api/dashboardApi';
import { loadReference } from './widgets/_shared/reference';

// Chatbot SPA-side action surface: see api/dashboardApi.ts.
installDashboardApi();

// Not wrapped in StrictMode: biApp's imperative renderActive() must fire
// exactly once per active-page change (the paginated_table renderer sets
// `ws.sort = options[0]` after the first render, so a second call re-sorts
// the rows).
// Fill the reference tables from the active country profile before the first
// render, so a widget never paints a stale region filter and then swaps it. The
// call never rejects and self-limits to a few seconds: on any failure the
// bundled tables stand and the dashboard renders exactly as it did before
// profiles existed.
loadReference().finally(() => {
  createRoot(document.getElementById('root')!).render(<App />);
});
